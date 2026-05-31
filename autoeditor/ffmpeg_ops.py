"""Low-level ffmpeg operations — single-pass pipeline.

All source clips are fed as inputs to a single ffmpeg call. A filter_complex
graph normalises every clip, builds crossfades within groups, applies fade-out
before midroll ads, hard-cuts between groups, and applies final fade-in/out —
all in one encode pass with no intermediate files.
"""

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .pipeline import Segment, SegmentType
from .silence import compute_keep_intervals


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
    fps = int(fps_num) / int(fps_den)

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
    "h264_nvenc": ["-preset", "p4", "-cq", "20"],
    "h264_amf":   ["-quality", "balanced", "-qp_i", "20", "-qp_p", "20"],
    "h264_qsv":   ["-preset", "fast", "-global_quality", "20"],
}


def _encode_args(config: Config) -> list[str]:
    """Return [-c:v <encoder> + quality flags] for the configured encoder."""
    enc = config.video_encoder
    quality = _ENCODER_QUALITY.get(enc, ["-preset", "fast", "-crf", "18"])
    return ["-c:v", enc] + quality


# ---------------------------------------------------------------------------
# Group splitting
# ---------------------------------------------------------------------------

def _split_into_groups(segments: list[Segment]) -> list[tuple[bool, list[int]]]:
    """Split segment indices into groups at midroll ad boundaries.

    Returns list of (is_midroll_ad, [segment_indices]).
    Each midroll ad is isolated into its own single-item group so that the
    surrounding content always hard-cuts to/from it.
    """
    groups: list[tuple[bool, list[int]]] = []
    current: list[int] = []

    for i, seg in enumerate(segments):
        if seg.type == SegmentType.MIDROLL_AD:
            if current:
                groups.append((False, current))
                current = []
            groups.append((True, [i]))
        else:
            current.append(i)

    if current:
        groups.append((False, current))

    return groups


# ---------------------------------------------------------------------------
# Duration helpers
# ---------------------------------------------------------------------------

def _group_duration(
    indices: list[int],
    clips: list[ClipInfo],
    fade_dur: float,
) -> float:
    """Effective output duration of a group, accounting for xfade overlap."""
    durations = [clips[i].effective_duration for i in indices]
    n = len(durations)
    if n <= 1 or fade_dur <= 0:
        return sum(durations)
    return sum(durations) - (n - 1) * fade_dur


# ---------------------------------------------------------------------------
# Filter graph builder
# ---------------------------------------------------------------------------

def _build_filter_complex(
    clips: list[ClipInfo],
    groups: list[tuple[bool, list[int]]],
    config: Config,
) -> tuple[str, str, str]:
    """Build the complete filter_complex for a single-pass render.

    Returns (filter_complex_string, v_out_label, a_out_label).

    Graph structure
    ---------------
    For each input clip i:
      [i:v] → scale/pad/fps/format    → [vni]
      [i:a] → aresample/aformat       → [ani]   (or aevalsrc if no audio)

    For each content group g:
      [vni][vnj]... → xfade chain     → [vxg]   (or hard concat if fade=0)
      [ani][anj]... → acrossfade chain → [axg]

    If group g precedes a midroll ad:
      [vxg] → fade=out                → [vfog]
      [axg] → afade=out               → [afog]

    All groups:
      [vg0][ag0][vg1][ag1]... → concat → [vpre][apre]

    Final:
      [vpre] → fade=in,fade=out       → [vout]
      [apre] → afade=in,afade=out     → [aout]
    """
    w, h = config.target_resolution.split("x")
    width, height = int(w), int(h)
    fps = config.target_fps
    fade_dur = config.fade_duration
    out_fade = config.output_fade_duration

    parts: list[str] = []

    # ------------------------------------------------------------------
    # 1. Per-clip normalisation
    #
    # When a clip carries keep_intervals (dead-space removal), each source
    # stream is first split, trimmed to every loud region, and concatenated
    # back together — butting the kept pieces edge-to-edge — before the usual
    # scale/fps/format normalisation. All segments come from the same source,
    # so their raw frames share size/format and concat needs no pre-normalise.
    # ------------------------------------------------------------------
    for i, clip in enumerate(clips):
        v_src, a_src = f"[{i}:v]", f"[{i}:a]"

        if clip.keep_intervals:
            k = len(clip.keep_intervals)
            v_outs = "".join(f"[vsr{i}_{j}]" for j in range(k))
            parts.append(f"[{i}:v]split={k}{v_outs}")
            v_trimmed = []
            for j, (a, b) in enumerate(clip.keep_intervals):
                parts.append(
                    f"[vsr{i}_{j}]trim=start={a:.3f}:end={b:.3f},"
                    f"setpts=PTS-STARTPTS[vtr{i}_{j}]"
                )
                v_trimmed.append(f"[vtr{i}_{j}]")
            parts.append(f"{''.join(v_trimmed)}concat=n={k}:v=1:a=0[vcat{i}]")
            v_src = f"[vcat{i}]"

            if clip.has_audio:
                a_outs = "".join(f"[asr{i}_{j}]" for j in range(k))
                parts.append(f"[{i}:a]asplit={k}{a_outs}")
                a_trimmed = []
                for j, (a, b) in enumerate(clip.keep_intervals):
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
            f"format=yuv420p"
            f"[vn{i}]"
        )
        if clip.has_audio:
            parts.append(
                f"{a_src}"
                f"aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo"
                f"[an{i}]"
            )
        else:
            # Synthesise a silent stereo track matching the clip duration.
            parts.append(
                f"aevalsrc=exprs=0:c=stereo:r=48000:d={clip.effective_duration:.6f}[an{i}]"
            )

    # ------------------------------------------------------------------
    # 2. Per-group processing
    # ------------------------------------------------------------------
    group_v: list[str] = []
    group_a: list[str] = []

    for g, (is_midroll, indices) in enumerate(groups):
        n = len(indices)

        if n == 1:
            # Single clip — pass the normalised labels through unchanged.
            v_label = f"vn{indices[0]}"
            a_label = f"an{indices[0]}"

        elif is_midroll or fade_dur <= 0:
            # Hard-cut concat within the group (midroll or crossfade disabled).
            inputs = "".join(f"[vn{i}][an{i}]" for i in indices)
            v_label = f"vhc{g}"
            a_label = f"ahc{g}"
            parts.append(
                f"{inputs}concat=n={n}:v=1:a=1[{v_label}][{a_label}]"
            )

        else:
            # xfade / acrossfade chain across all clips in the group.
            durations = [clips[i].effective_duration for i in indices]
            cumulative = 0.0

            for k in range(n - 1):
                offset = max(0.0, cumulative + durations[k] - (k + 1) * fade_dur)
                cumulative += durations[k]

                is_last_pair = (k == n - 2)
                v_in1 = f"[vn{indices[0]}]"  if k == 0 else f"[vxi{g}_{k}]"
                v_in2 = f"[vn{indices[k + 1]}]"
                v_out = f"[vx{g}]"           if is_last_pair else f"[vxi{g}_{k + 1}]"

                a_in1 = f"[an{indices[0]}]"  if k == 0 else f"[axi{g}_{k}]"
                a_in2 = f"[an{indices[k + 1]}]"
                a_out = f"[ax{g}]"           if is_last_pair else f"[axi{g}_{k + 1}]"

                parts.append(
                    f"{v_in1}{v_in2}"
                    f"xfade=transition=fade:duration={fade_dur}:offset={offset:.3f}"
                    f"{v_out}"
                )
                parts.append(
                    f"{a_in1}{a_in2}"
                    f"acrossfade=d={fade_dur}:c1=tri:c2=tri"
                    f"{a_out}"
                )

            v_label = f"vx{g}"
            a_label = f"ax{g}"

        # Fade-out at the end of any content group that precedes a midroll ad.
        next_is_midroll = (g + 1 < len(groups)) and groups[g + 1][0]
        if not is_midroll and next_is_midroll and out_fade > 0:
            grp_dur = _group_duration(indices, clips, fade_dur)
            fo_start = max(0.0, grp_dur - out_fade)
            parts.append(
                f"[{v_label}]fade=t=out:st={fo_start:.3f}:d={out_fade}[vfo{g}]"
            )
            parts.append(
                f"[{a_label}]afade=t=out:st={fo_start:.3f}:d={out_fade}[afo{g}]"
            )
            v_label = f"vfo{g}"
            a_label = f"afo{g}"

        group_v.append(v_label)
        group_a.append(a_label)

    # ------------------------------------------------------------------
    # 3. Hard-cut concat across all groups
    # ------------------------------------------------------------------
    if len(groups) == 1:
        v_pre, a_pre = group_v[0], group_a[0]
    else:
        interleaved = "".join(
            f"[{v}][{a}]" for v, a in zip(group_v, group_a)
        )
        parts.append(
            f"{interleaved}concat=n={len(groups)}:v=1:a=1[vpre][apre]"
        )
        v_pre, a_pre = "vpre", "apre"

    # ------------------------------------------------------------------
    # 4. Final fade-in at the very start, fade-out at the very end
    # ------------------------------------------------------------------
    if out_fade > 0:
        total_dur = sum(
            _group_duration(indices, clips, fade_dur if not is_midroll else 0)
            for is_midroll, indices in groups
        )
        fo_start = max(0.0, total_dur - out_fade)
        parts.append(
            f"[{v_pre}]"
            f"fade=t=in:st=0:d={out_fade},"
            f"fade=t=out:st={fo_start:.3f}:d={out_fade}"
            f"[vout]"
        )
        parts.append(
            f"[{a_pre}]"
            f"afade=t=in:st=0:d={out_fade},"
            f"afade=t=out:st={fo_start:.3f}:d={out_fade}"
            f"[aout]"
        )
        v_out_label, a_out_label = "vout", "aout"
    else:
        v_out_label, a_out_label = v_pre, a_pre

    return ";".join(parts), v_out_label, a_out_label


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def _fmt_time(seconds: float) -> str:
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
            timing = f"{_fmt_time(elapsed)} / {_fmt_time(total_dur)} ({pct:.0f}%)"
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

def render_project(
    segments: list[Segment],
    output_path: Path,
    config: Config,
    *,
    verbose: bool = True,
) -> Path:
    """Single-pass render: probe all clips → build filter_complex → one ffmpeg call.

    No intermediate files are written. All normalisation, crossfading, and
    fading happens inside a single filter_complex graph.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    # Step 1: Probe (reads metadata only — no decode)
    log("  Probing clips...")
    clips: list[ClipInfo] = []
    for seg in segments:
        log(f"    {seg.label}: {seg.path.name}")
        clip = probe_clip(seg.path)

        # Dead-space removal applies only to game recordings — branded assets
        # (intro/outro/transition/midroll) are already tightly edited.
        if config.remove_dead_space and seg.type == SegmentType.GAME and clip.has_audio:
            clip.keep_intervals = compute_keep_intervals(
                seg.path, clip.duration, config
            )
            if clip.keep_intervals:
                removed = clip.duration - clip.effective_duration
                log(
                    f"      dead space: {len(clip.keep_intervals)} segment(s) kept, "
                    f"{_fmt_time(removed)} trimmed "
                    f"({_fmt_time(clip.duration)} → {_fmt_time(clip.effective_duration)})"
                )

        clips.append(clip)

    # Step 2: Build filter graph
    groups = _split_into_groups(segments)
    log(f"  Building filter graph ({len(segments)} clips, {len(groups)} group(s))...")
    filter_complex, v_out, a_out = _build_filter_complex(clips, groups, config)

    # Step 3: Single ffmpeg call — all inputs, one encode pass.
    # -progress and -nostats are global options and must precede all -i flags.
    cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-nostats"]
    for seg in segments:
        cmd += ["-i", str(seg.path)]

    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{v_out}]",
        "-map", f"[{a_out}]",
        *_encode_args(config),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        str(output_path),
    ]

    if verbose:
        total_dur = sum(
            _group_duration(indices, clips, config.fade_duration if not is_midroll else 0)
            for is_midroll, indices in groups
        )
        _run_with_progress(cmd, total_dur)
    else:
        subprocess.run(cmd, check=True, capture_output=True)

    log(f"  Output: {output_path}")
    return output_path
