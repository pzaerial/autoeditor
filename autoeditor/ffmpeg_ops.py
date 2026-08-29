"""Low-level ffmpeg operations -- single-pass pipeline.

All source clips are fed as inputs to a single ffmpeg call. A filter_complex
graph normalises every clip, builds crossfades where the script asks for them,
fades to and from black across `fade` joins, hard-cuts across `cut` joins, and
applies the final fade-in/out -- all in one encode pass with no intermediate
files.

The assembly order comes entirely from the parsed script's clip list; nothing
here knows what an intro or a midroll ad is.
"""

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .silence import compute_keep_intervals
from .timeline import Join, TimelineClip, VideoScript


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

@dataclass
class ClipInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    # Loud (keep) regions in seconds when dead-space removal trims this clip.
    # None means the clip plays in full.
    keep_intervals: list[tuple[float, float]] | None = None

    @property
    def effective_duration(self) -> float:
        """Output duration after dead-space trimming (full duration if none)."""
        if self.keep_intervals is None:
            return self.duration
        return sum(b - a for a, b in self.keep_intervals)


def probe_clip(path: Path) -> ClipInfo:
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-show_format",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)

    video_stream = next(
        (s for s in data["streams"] if s["codec_type"] == "video"), None
    )
    if video_stream is None:
        raise ValueError(f"No video stream in {path}")

    audio_streams = [s for s in data["streams"] if s["codec_type"] == "audio"]

    fps_num, fps_den = video_stream.get("r_frame_rate", "30/1").split("/")
    fps = int(fps_num) / int(fps_den) if int(fps_den) else 0.0

    return ClipInfo(
        path=path,
        duration=float(data["format"].get("duration", 0)),
        width=int(video_stream["width"]),
        height=int(video_stream["height"]),
        fps=fps,
        has_audio=bool(audio_streams),
    )


# ---------------------------------------------------------------------------
# Encoder helpers
# ---------------------------------------------------------------------------

_ENCODER_QUALITY: dict[str, list[str]] = {
    "libx264":    ["-preset", "fast", "-crf", "18"],
    "libx265":    ["-preset", "fast", "-crf", "22"],
    "h264_nvenc": ["-preset", "p4", "-cq", "20"],
    "hevc_nvenc": ["-preset", "p4", "-cq", "22"],
    "h264_amf":   ["-quality", "balanced", "-qp_i", "20", "-qp_p", "20"],
    "h264_qsv":   ["-preset", "fast", "-global_quality", "20"],
}


def _encode_args(encoder: str) -> list[str]:
    """Return [-c:v <encoder> + quality flags] for the configured encoder."""
    quality = _ENCODER_QUALITY.get(encoder, ["-preset", "fast", "-crf", "18"])
    return ["-c:v", encoder] + quality


# ---------------------------------------------------------------------------
# Group splitting
# ---------------------------------------------------------------------------

def _split_into_groups(clips: list[TimelineClip]) -> list[list[int]]:
    """Split clip indices into groups at every non-crossfade join.

    Clips joined by `crossfade` share a group and are blended with an xfade
    chain. `cut` and `fade` joins end the current group, because both hard-cut
    the assembled streams (a `fade` join just darkens each side first).
    """
    groups: list[list[int]] = []
    current: list[int] = []

    for i, clip in enumerate(clips):
        if i > 0 and clip.join is not Join.CROSSFADE and current:
            groups.append(current)
            current = []
        current.append(i)

    if current:
        groups.append(current)

    return groups


# ---------------------------------------------------------------------------
# Duration helpers
# ---------------------------------------------------------------------------

def _group_duration(
    indices: list[int], infos: list[ClipInfo], clips: list[TimelineClip]
) -> float:
    """Effective output duration of a group, accounting for xfade overlap."""
    total = infos[indices[0]].effective_duration
    for i in indices[1:]:
        total += infos[i].effective_duration - clips[i].join_duration
    return total


def _total_duration(
    groups: list[list[int]], infos: list[ClipInfo], clips: list[TimelineClip]
) -> float:
    return sum(_group_duration(g, infos, clips) for g in groups)


# ---------------------------------------------------------------------------
# Filter graph builder
# ---------------------------------------------------------------------------

def _build_filter_complex(
    infos: list[ClipInfo],
    clips: list[TimelineClip],
    groups: list[list[int]],
    script: VideoScript,
) -> tuple[str, str, str]:
    """Build the complete filter_complex for a single-pass render.

    Returns (filter_complex_string, v_out_label, a_out_label).

    Graph structure
    ---------------
    For each input clip i:
      [i:v] -> (trim/concat if silence removed) -> scale/pad/fps/format -> [vni]
      [i:a] -> (atrim/concat if silence removed) -> aresample/aformat   -> [ani]
               (or aevalsrc when the source has no audio track)

    For each group g (clips joined by crossfade):
      [vni][vnj]... -> xfade chain      -> [vxg]
      [ani][anj]... -> acrossfade chain -> [axg]

    Where a `fade` join meets a group boundary:
      the group before it gains fade=out / afade=out,
      the group after it gains fade=in / afade=in.

    All groups:
      [vg0][ag0][vg1][ag1]... -> concat -> [vpre][apre]

    Final:
      [vpre] -> fade=in,fade=out   -> [vout]
      [apre] -> afade=in,afade=out -> [aout]
    """
    width, height = script.output.size
    fps = script.output.fps
    in_fade = script.output.fade_in
    out_fade = script.output.fade_out

    parts: list[str] = []

    # ------------------------------------------------------------------
    # 1. Per-clip normalisation
    #
    # When a clip carries keep_intervals (dead-space removal), each source
    # stream is first split, trimmed to every loud region, and concatenated
    # back together -- butting the kept pieces edge-to-edge -- before the usual
    # scale/fps/format normalisation. All segments come from the same source,
    # so their raw frames share size/format and concat needs no pre-normalise.
    # ------------------------------------------------------------------
    for i, info in enumerate(infos):
        v_src, a_src = f"[{i}:v]", f"[{i}:a]"

        if info.keep_intervals:
            k = len(info.keep_intervals)
            v_outs = "".join(f"[vsr{i}_{j}]" for j in range(k))
            parts.append(f"[{i}:v]split={k}{v_outs}")
            v_trimmed = []
            for j, (a, b) in enumerate(info.keep_intervals):
                parts.append(
                    f"[vsr{i}_{j}]trim=start={a:.3f}:end={b:.3f},"
                    f"setpts=PTS-STARTPTS[vtr{i}_{j}]"
                )
                v_trimmed.append(f"[vtr{i}_{j}]")
            parts.append(f"{''.join(v_trimmed)}concat=n={k}:v=1:a=0[vcat{i}]")
            v_src = f"[vcat{i}]"

            if info.has_audio:
                a_outs = "".join(f"[asr{i}_{j}]" for j in range(k))
                parts.append(f"[{i}:a]asplit={k}{a_outs}")
                a_trimmed = []
                for j, (a, b) in enumerate(info.keep_intervals):
                    parts.append(
                        f"[asr{i}_{j}]atrim=start={a:.3f}:end={b:.3f},"
                        f"asetpts=PTS-STARTPTS[atr{i}_{j}]"
                    )
                    a_trimmed.append(f"[atr{i}_{j}]")
                parts.append(f"{''.join(a_trimmed)}concat=n={k}:v=0:a=1[acat{i}]")
                a_src = f"[acat{i}]"

        parts.append(
            f"{v_src}"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},"
            f"format=yuv420p,"
            f"setsar=1"
            f"[vn{i}]"
        )
        if info.has_audio:
            parts.append(
                f"{a_src}"
                f"aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo"
                f"[an{i}]"
            )
        else:
            # Synthesise a silent stereo track matching the clip duration.
            parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000:"
                f"duration={info.effective_duration:.6f},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo"
                f"[an{i}]"
            )

    # ------------------------------------------------------------------
    # 2. Per-group crossfade chains
    # ------------------------------------------------------------------
    group_v: list[str] = []
    group_a: list[str] = []

    for g, indices in enumerate(groups):
        n = len(indices)

        if n == 1:
            v_label = f"vn{indices[0]}"
            a_label = f"an{indices[0]}"
        else:
            # xfade / acrossfade chain across all clips in the group. Each pair
            # may blend for a different duration, so the offset is tracked as a
            # running total of the chain's own output length.
            acc = infos[indices[0]].effective_duration

            for k in range(n - 1):
                nxt = indices[k + 1]
                blend = clips[nxt].join_duration
                offset = max(0.0, acc - blend)
                acc = acc + infos[nxt].effective_duration - blend

                is_last_pair = (k == n - 2)
                v_in1 = f"[vn{indices[0]}]" if k == 0 else f"[vxi{g}_{k}]"
                v_out = f"[vx{g}]" if is_last_pair else f"[vxi{g}_{k + 1}]"
                a_in1 = f"[an{indices[0]}]" if k == 0 else f"[axi{g}_{k}]"
                a_out = f"[ax{g}]" if is_last_pair else f"[axi{g}_{k + 1}]"

                parts.append(
                    f"{v_in1}[vn{nxt}]"
                    f"xfade=transition=fade:duration={blend}:offset={offset:.3f}"
                    f"{v_out}"
                )
                parts.append(
                    f"{a_in1}[an{nxt}]"
                    f"acrossfade=d={blend}:c1=tri:c2=tri"
                    f"{a_out}"
                )

            v_label = f"vx{g}"
            a_label = f"ax{g}"

        # --------------------------------------------------------------
        # 3. Fades to/from black at `fade` join boundaries
        # --------------------------------------------------------------
        v_chain: list[str] = []
        a_chain: list[str] = []

        # A fade join opening this group fades it up from black.
        if g > 0 and clips[indices[0]].join is Join.FADE:
            up = clips[indices[0]].join_duration
            v_chain.append(f"fade=t=in:st=0:d={up}")
            a_chain.append(f"afade=t=in:st=0:d={up}")

        # A fade join opening the *next* group fades this one down to black.
        if g + 1 < len(groups):
            nxt_first = groups[g + 1][0]
            if clips[nxt_first].join is Join.FADE:
                down = clips[nxt_first].join_duration
                start = max(0.0, _group_duration(indices, infos, clips) - down)
                v_chain.append(f"fade=t=out:st={start:.3f}:d={down}")
                a_chain.append(f"afade=t=out:st={start:.3f}:d={down}")

        if v_chain:
            parts.append(f"[{v_label}]{','.join(v_chain)}[vgf{g}]")
            parts.append(f"[{a_label}]{','.join(a_chain)}[agf{g}]")
            v_label, a_label = f"vgf{g}", f"agf{g}"

        group_v.append(v_label)
        group_a.append(a_label)

    # ------------------------------------------------------------------
    # 4. Hard-cut concat across all groups
    # ------------------------------------------------------------------
    if len(groups) == 1:
        v_pre, a_pre = group_v[0], group_a[0]
    else:
        interleaved = "".join(f"[{v}][{a}]" for v, a in zip(group_v, group_a))
        parts.append(f"{interleaved}concat=n={len(groups)}:v=1:a=1[vpre][apre]")
        v_pre, a_pre = "vpre", "apre"

    # ------------------------------------------------------------------
    # 5. Final fade-in at the very start, fade-out at the very end
    # ------------------------------------------------------------------
    v_final: list[str] = []
    a_final: list[str] = []

    if in_fade > 0:
        v_final.append(f"fade=t=in:st=0:d={in_fade}")
        a_final.append(f"afade=t=in:st=0:d={in_fade}")
    if out_fade > 0:
        total = _total_duration(groups, infos, clips)
        start = max(0.0, total - out_fade)
        v_final.append(f"fade=t=out:st={start:.3f}:d={out_fade}")
        a_final.append(f"afade=t=out:st={start:.3f}:d={out_fade}")

    if v_final:
        parts.append(f"[{v_pre}]{','.join(v_final)}[vout]")
        parts.append(f"[{a_pre}]{','.join(a_final)}[aout]")
        return ";".join(parts), "vout", "aout"

    return ";".join(parts), v_pre, a_pre


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def format_time(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _run_with_progress(cmd: list[str], total_dur: float) -> None:
    """Run an ffmpeg command and stream a live progress line to the console.

    -progress pipe:1 must be placed as a global option (before -i flags) so
    ffmpeg writes key=value progress data to stdout. A background thread drains
    stderr concurrently to prevent the pipe buffer from filling and deadlocking.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    # Drain stderr in a background thread to prevent pipe-buffer deadlock.
    # ffmpeg writes filter graph analysis and encoder setup there before any
    # progress data appears on stdout, which can fill the 64 KB buffer.
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    out_time_us = 0
    speed = ""

    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.strip()
        if line.startswith("out_time_us="):
            try:
                out_time_us = int(line.split("=", 1)[1])
            except ValueError:
                pass
        elif line.startswith("speed="):
            speed = line.split("=", 1)[1].strip()
        elif line.startswith("progress="):
            elapsed = out_time_us / 1_000_000
            pct = min(100.0, (elapsed / total_dur) * 100) if total_dur > 0 else 0
            timing = f"{format_time(elapsed)} / {format_time(total_dur)} ({pct:.0f}%)"
            speed_str = f"  @ {speed}" if speed not in ("", "N/A") else ""
            print(f"\r  Rendering: {timing}{speed_str}   ", end="", flush=True)

    proc.wait()
    stderr_thread.join()
    print()  # move past the progress line

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, stderr="".join(stderr_lines)
        )


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def probe_script(script: VideoScript, *, verbose: bool = True) -> list[ClipInfo]:
    """Probe every clip in the script, running silence detection where asked.

    Detection results are cached per source file so a clip reused several times
    in the timeline is only analysed once.
    """
    def log(msg: str) -> None:
        if verbose:
            print(msg)

    infos: list[ClipInfo] = []
    cache: dict[Path, list[tuple[float, float]] | None] = {}

    for clip in script.clips:
        info = probe_clip(clip.path)

        if clip.trim_silence and info.has_audio:
            if clip.path not in cache:
                log(f"    analysing silence: {clip.path.name}")
                cache[clip.path] = compute_keep_intervals(
                    clip.path, info.duration, script.silence
                )
            info.keep_intervals = cache[clip.path]

            if info.keep_intervals:
                removed = info.duration - info.effective_duration
                log(
                    f"      {len(info.keep_intervals)} segment(s) kept, "
                    f"{format_time(removed)} trimmed "
                    f"({format_time(info.duration)} -> "
                    f"{format_time(info.effective_duration)})"
                )

        infos.append(info)

    return infos


def render_script(
    script: VideoScript,
    infos: list[ClipInfo],
    *,
    verbose: bool = True,
) -> Path:
    """Single-pass render: build filter_complex from the script, one ffmpeg call.

    No intermediate files are written. All normalisation, crossfading, and
    fading happens inside a single filter_complex graph.
    """
    output_path = script.output.file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clips = script.clips
    groups = _split_into_groups(clips)

    if verbose:
        print(f"  Building filter graph ({len(clips)} clips, {len(groups)} group(s))...")

    filter_complex, v_out, a_out = _build_filter_complex(infos, clips, groups, script)

    # -progress and -nostats are global options and must precede all -i flags.
    cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-nostats"]
    for clip in clips:
        cmd += ["-i", str(clip.path)]

    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{v_out}]",
        "-map", f"[{a_out}]",
        *_encode_args(script.output.encoder),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(output_path),
    ]

    if verbose:
        _run_with_progress(cmd, _total_duration(groups, infos, clips))
    else:
        subprocess.run(cmd, check=True, capture_output=True)

    return output_path
