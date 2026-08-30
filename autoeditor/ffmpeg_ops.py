"""Single-pass ffmpeg render: every clip is an input to one filter_complex."""

import json
import subprocess
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from .loudness import balance_gain, measure_loudness
from .silence import compute_keep_intervals
from .timeline import Join, Region, TimelineClip, VideoScript


@dataclass
class ClipInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    vcodec: str = ""
    acodec: str = ""
    pix_fmt: str = ""
    # Levelling trim resolved for this clip, 0 when balancing is off.
    balance_db: float = 0.0
    # Loud regions to keep; None means the clip plays in full.
    keep_intervals: list[tuple[float, float]] | None = None

    @property
    def effective_duration(self) -> float:
        """Output duration after dead-space trimming."""
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
        vcodec=video_stream.get("codec_name", ""),
        acodec=audio_streams[0].get("codec_name", "") if audio_streams else "",
        pix_fmt=video_stream.get("pix_fmt", ""),
    )


def _cpu(quality: int) -> list[str]:
    return ["-preset", "fast", "-crf", str(quality)]


def _nvenc(quality: int) -> list[str]:
    # The rate control has to be named. Left to its default, nvenc honours -cq
    # only loosely and writes roughly 2.4x the size of libx264 for the same
    # wall time -- measured, and the reason this is not just ["-cq", n].
    return ["-preset", "p4", "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]


def _amf(quality: int) -> list[str]:
    return ["-quality", "balanced", "-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(quality)]


def _qsv(quality: int) -> list[str]:
    return ["-preset", "fast", "-global_quality", str(quality)]


# Default quality per encoder, then how that number is spelled for it. The
# scale is CRF-like throughout: lower is better and bigger.
_ENCODER_PROFILES: dict[str, tuple[int, object]] = {
    "libx264":    (18, _cpu),
    "libx265":    (22, _cpu),
    "h264_nvenc": (23, _nvenc),
    "hevc_nvenc": (25, _nvenc),
    "av1_nvenc":  (25, _nvenc),
    "h264_amf":   (22, _amf),
    "hevc_amf":   (24, _amf),
    "h264_qsv":   (22, _qsv),
    "hevc_qsv":   (24, _qsv),
}


def encoder_default_quality(encoder: str) -> int:
    return _ENCODER_PROFILES.get(encoder, (18, _cpu))[0]


def _encode_args(encoder: str, quality: float | None = None) -> list[str]:
    default, build = _ENCODER_PROFILES.get(encoder, (18, _cpu))
    return ["-c:v", encoder] + build(int(default if quality is None else quality))


_ENCODER_SUPPORT: dict[str, bool] = {}


def encoder_available(encoder: str) -> bool:
    """Whether this machine can actually encode with it, cached per process.

    A hardware encoder can be compiled into ffmpeg and still fail to open --
    the dropdown offered AMD and Intel encoders on a machine with neither,
    which only came out as a failed render.
    """
    if encoder not in _ENCODER_SUPPORT:
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=0.2",
                    *_encode_args(encoder), "-f", "null", "-",
                ],
                capture_output=True, timeout=30,
            )
            _ENCODER_SUPPORT[encoder] = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _ENCODER_SUPPORT[encoder] = False
    return _ENCODER_SUPPORT[encoder]


def expand_regions(clips: list[TimelineClip]) -> list[TimelineClip]:
    """Split any clip whose regions blend into one clip per region.

    Regions joined by plain cuts stay together and are dropped in-graph by
    keep_intervals, which needs only one ffmpeg input. A region that crossfades
    or fades into the one before it cannot be expressed that way, so the clip
    becomes several clips and the ordinary join machinery takes over.
    """
    out: list[TimelineClip] = []
    for clip in clips:
        if not clip.needs_region_split():
            out.append(clip)
            continue
        for i, region in enumerate(clip.regions or []):
            out.append(replace(
                clip,
                regions=[replace(region, join=Join.CUT, join_duration=0.0)],
                join=clip.join if i == 0 else region.join,
                join_duration=clip.join_duration if i == 0 else region.join_duration,
                balance_db=clip.balance_db,
                audio_overlap=clip.audio_overlap if i == 0 else None,
                audio_lead=clip.audio_lead if i == 0 else 0.0,
                label=clip.label if i == 0 else f"{clip.label} ({i + 1})",
            ))
    return out


def _clamp(intervals, duration: float) -> list[tuple[float, float]]:
    """Trim intervals to the clip's real length, dropping any that fall outside."""
    out = []
    for a, b in intervals:
        a, b = max(0.0, a), min(duration, b)
        if b - a > 1e-6:
            out.append((a, b))
    return out


def _intersect(left, right) -> list[tuple[float, float]]:
    """Overlapping portions of two sorted interval lists."""
    out, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        a = max(left[i][0], right[j][0])
        b = min(left[i][1], right[j][1])
        if b - a > 1e-6:
            out.append((a, b))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return out


def _split_into_groups(clips: list[TimelineClip]) -> list[list[int]]:
    """Group clips into runs joined by crossfade; cut and fade joins end a run."""
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


def _group_duration(
    indices: list[int], infos: list[ClipInfo], clips: list[TimelineClip]
) -> float:
    """Output duration of a group, accounting for xfade overlap."""
    total = infos[indices[0]].effective_duration
    for i in indices[1:]:
        total += infos[i].effective_duration - clips[i].join_duration
    return total


def _total_duration(
    groups: list[list[int]], infos: list[ClipInfo], clips: list[TimelineClip]
) -> float:
    return sum(_group_duration(g, infos, clips) for g in groups)


def _video_offsets(
    infos: list[ClipInfo], clips: list[TimelineClip], groups: list[list[int]]
) -> list[float]:
    """Output time each clip's picture starts at.

    The same arithmetic the xfade offsets use, pulled out so the audio layout
    cannot drift from the picture it is placed against.
    """
    offsets = [0.0] * len(clips)
    base = 0.0
    for indices in groups:
        acc = base
        offsets[indices[0]] = acc
        for k in range(1, len(indices)):
            acc += infos[indices[k - 1]].effective_duration - clips[indices[k]].join_duration
            offsets[indices[k]] = acc
        base += _group_duration(indices, infos, clips)
    return offsets


def _uses_audio_offsets(clips: list[TimelineClip]) -> bool:
    """True when any join asks its sound to depart from its picture."""
    return any(not clip.audio_follows_picture() for clip in clips[1:])


def _audio_layout(
    infos: list[ClipInfo], clips: list[TimelineClip], groups: list[list[int]]
) -> list[dict]:
    """Where each clip's audio sits, what it plays, and how its edges blend.

    A join's audio transition is a crossfade of `audio_blend` seconds whose
    centre sits `audio_lead` seconds before the picture cut. Both are paid for
    out of the clips' own source either side of their in and out points -- so
    the timeline never shifts, the total length is still the picture's, and
    only the join you asked about loses lock with the picture.

        head = lead + (blend - join_duration) / 2   extra source before the in point
        tail = (blend - join_duration) / 2 - lead   extra source after the out point

    With no lead and a blend matching the picture, both are zero and every clip
    lands exactly where the concat/acrossfade chain would have put it.
    """
    starts = _video_offsets(infos, clips, groups)
    count = len(clips)
    ranges = [info.keep_intervals or [(0.0, info.duration)] for info in infos]

    head = [0.0] * count
    tail = [0.0] * count
    blend = [0.0] * (count + 1)
    short = [None] * count   # per join: (asked, granted) when the source ran out

    for i in range(1, count):
        span = clips[i].join_duration
        half = (clips[i].audio_blend - span) / 2
        want_head = clips[i].audio_lead + half
        want_tail = half - clips[i].audio_lead

        # Only what the source actually has either side, and never so much
        # that a clip is asked to give up its own middle.
        room_head = ranges[i][0][0]
        room_tail = infos[i - 1].duration - ranges[i - 1][-1][1]
        head[i] = min(max(want_head, -0.45 * infos[i].effective_duration), room_head)
        tail[i - 1] = min(
            max(want_tail, -0.45 * infos[i - 1].effective_duration), room_tail
        )
        blend[i] = max(0.0, span + head[i] + tail[i - 1])

        # An overlap is paid for out of source either side of the cut. A clip
        # used to its last frame has none to give, and the ask quietly shrinks
        # -- so say so rather than letting the edit look broken.
        asked = span + want_head + want_tail
        if asked - blend[i] > 0.01:
            short[i] = (asked, blend[i])

    layout = []
    for i, info in enumerate(infos):
        spans = [[a, b] for a, b in ranges[i]]
        spans[0][0] -= head[i]
        spans[-1][1] += tail[i]
        kept = [
            (max(0.0, a), min(info.duration, b))
            for a, b in spans
            if min(info.duration, b) - max(0.0, a) > 1e-6
        ]
        layout.append({
            "shortfall": short[i],
            "start": max(0.0, starts[i] - head[i]),
            "intervals": kept,
            "duration": sum(b - a for a, b in kept),
            "fade_in": blend[i],
            "fade_out": blend[i + 1],
        })
    return layout


def audio_notes(script: VideoScript, infos: list[ClipInfo]) -> list[str]:
    """Joins whose audio overlap the sources could not fully pay for."""
    clips = expand_regions(script.clips)
    if not _uses_audio_offsets(clips):
        return []
    layout = _audio_layout(infos, clips, _split_into_groups(clips))
    notes = []
    for clip, item in zip(clips, layout):
        if not item["shortfall"]:
            continue
        asked, got = item["shortfall"]
        notes.append(
            f"{clip.label}: audio overlap {asked:.2f}s trimmed to {got:.2f}s -- "
            f"the sources either side of the cut have no more audio to give. "
            f"Trim a clip's picture back to leave some."
        )
    return notes


def _trim_concat(index: int, intervals, kind: str) -> tuple[list[str], str]:
    """split/trim/concat one stream down to the intervals worth keeping."""
    if len(intervals) == 1 and kind == "a":
        a, b = intervals[0]
        return (
            [f"[{index}:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[aone{index}]"],
            f"[aone{index}]",
        )
    n = len(intervals)
    outs = "".join(f"[asr{index}_{j}]" for j in range(n))
    parts = [f"[{index}:a]asplit={n}{outs}"]
    trimmed = []
    for j, (a, b) in enumerate(intervals):
        parts.append(
            f"[asr{index}_{j}]atrim=start={a:.3f}:end={b:.3f},"
            f"asetpts=PTS-STARTPTS[atr{index}_{j}]"
        )
        trimmed.append(f"[atr{index}_{j}]")
    parts.append(f"{''.join(trimmed)}concat=n={n}:v=0:a=1[acat{index}]")
    return parts, f"[acat{index}]"


def _place_audio(
    infos: list[ClipInfo],
    clips: list[TimelineClip],
    groups: list[list[int]],
    script: VideoScript,
    total: float,
) -> tuple[list[str], str]:
    """Build the audio as placed, mixed segments rather than one concat chain.

    Only used when a join actually asks for it. The ordinary path stays a
    concat/acrossfade chain: it is what every existing project renders through,
    it pulls each source only when the timeline reaches it, and this mix is
    strictly more machinery for the same result when nothing is offset.
    """
    layout = _audio_layout(infos, clips, groups)
    master = script.globals.audio_gain_db
    parts: list[str] = []
    placed: list[str] = []

    for i, (info, item) in enumerate(zip(infos, layout)):
        # A silent clip contributes silence; leaving it out of the mix says
        # the same thing for less work.
        if not info.has_audio or item["duration"] <= 0:
            continue

        made, source = _trim_concat(i, item["intervals"], "a")
        parts += made

        chain = ["aresample=48000"]
        gain = master + clips[i].audio_gain_db + info.balance_db
        if abs(gain) > 1e-9:
            chain.append(f"volume={gain:g}dB")
        chain.append(_pin(item["duration"]).rstrip(","))
        if item["fade_in"] > 0:
            chain.append(f"afade=t=in:st=0:d={item['fade_in']:.3f}")
        if item["fade_out"] > 0:
            start = max(0.0, item["duration"] - item["fade_out"])
            chain.append(f"afade=t=out:st={start:.3f}:d={item['fade_out']:.3f}")
        chain.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
        # adelay works in whole milliseconds -- a fifteenth of a frame at 60fps.
        offset = int(round(item["start"] * 1000))
        if offset:
            chain.append(f"adelay={offset}|{offset}")
        parts.append(f"{source}{','.join(chain)}[ap{i}]")
        placed.append(f"[ap{i}]")

    if not placed:
        parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={total:.6f},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[apre]"
        )
        return parts, "apre"

    parts.append(
        f"{''.join(placed)}amix=inputs={len(placed)}:normalize=0:"
        f"dropout_transition=0[amixed]"
    )
    # The picture decides how long the video is; pad the mix out to match.
    parts.append(f"[amixed]apad=whole_dur={total:.6f}[apre]")
    return parts, "apre"


# -1 dBFS, as linear amplitude, which is what alimiter wants.
OUTPUT_CEILING = 10 ** (-1.0 / 20)


def _pin(duration: float) -> str:
    """Force an audio stream to exactly `duration`, padding or clipping it.

    A decoder's idea of a clip's length is not the timeline's: AAC pads its
    final frame by up to ~20 ms, and a capture can hand back an audio track
    shorter than its video. Concatenation believes whatever it is given, so
    without this each clip's slack pushes every later clip out of step with its
    own picture -- a drift that grows with the clip count rather than cancelling.
    """
    return f"apad,atrim=end={duration:.6f},asetpts=PTS-STARTPTS,"


def _build_filter_complex(
    infos: list[ClipInfo],
    clips: list[TimelineClip],
    groups: list[list[int]],
    script: VideoScript,
) -> tuple[str, str, str]:
    """Build the filter_complex, returning it with the output video/audio labels."""
    width, height = script.output.size
    fps = script.output.fps
    in_fade = script.globals.fade_in
    out_fade = script.globals.fade_out
    master_gain = script.globals.audio_gain_db

    parts: list[str] = []
    # When a join wants its sound off its picture, audio is placed and mixed
    # instead of chained; the per-clip audio normalising below is then skipped.
    placed = _uses_audio_offsets(clips)

    # Per-clip: drop dead space in-graph, then normalise size, rate and format.
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

            if info.has_audio and not placed:
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
        if placed:
            continue
        if info.has_audio:
            # The project-wide adjust and this clip's own trim are one gain.
            gain = master_gain + clips[i].audio_gain_db + info.balance_db
            level = f"volume={gain:g}dB," if abs(gain) > 1e-9 else ""
            parts.append(
                f"{a_src}"
                f"aresample=48000,"
                f"{level}"
                f"{_pin(info.effective_duration)}"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo"
                f"[an{i}]"
            )
        else:
            parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000:"
                f"duration={info.effective_duration:.6f},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo"
                f"[an{i}]"
            )

    group_v: list[str] = []
    group_a: list[str] = []

    for g, indices in enumerate(groups):
        n = len(indices)

        if n == 1:
            v_label = f"vn{indices[0]}"
            a_label = f"an{indices[0]}"
        else:
            # Offsets track the running output length, as blend durations vary per pair.
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
                if not placed:
                    parts.append(
                        f"{a_in1}[an{nxt}]"
                        f"acrossfade=d={blend}:c1=tri:c2=tri"
                        f"{a_out}"
                    )

            v_label = f"vx{g}"
            a_label = f"ax{g}"

        v_chain: list[str] = []
        a_chain: list[str] = []

        # A fade join opening this group fades it up from black.
        if g > 0 and clips[indices[0]].join is Join.FADE:
            up = clips[indices[0]].join_duration
            v_chain.append(f"fade=t=in:st=0:d={up}")
            a_chain.append(f"afade=t=in:st=0:d={up}")

        # A fade join opening the next group fades this one down to black.
        if g + 1 < len(groups):
            nxt_first = groups[g + 1][0]
            if clips[nxt_first].join is Join.FADE:
                down = clips[nxt_first].join_duration
                start = max(0.0, _group_duration(indices, infos, clips) - down)
                v_chain.append(f"fade=t=out:st={start:.3f}:d={down}")
                a_chain.append(f"afade=t=out:st={start:.3f}:d={down}")

        if v_chain:
            parts.append(f"[{v_label}]{','.join(v_chain)}[vgf{g}]")
            v_label = f"vgf{g}"
            if not placed:
                parts.append(f"[{a_label}]{','.join(a_chain)}[agf{g}]")
                a_label = f"agf{g}"

        group_v.append(v_label)
        group_a.append(a_label)

    total = _total_duration(groups, infos, clips)

    if placed:
        # Video concats on its own; audio is mixed from its placed segments.
        if len(groups) == 1:
            v_pre = group_v[0]
        else:
            joined = "".join(f"[{v}]" for v in group_v)
            parts.append(f"{joined}concat=n={len(groups)}:v=1:a=0[vpre]")
            v_pre = "vpre"
        audio_parts, a_pre = _place_audio(infos, clips, groups, script, total)
        parts += audio_parts
    elif len(groups) == 1:
        v_pre, a_pre = group_v[0], group_a[0]
    else:
        interleaved = "".join(f"[{v}][{a}]" for v, a in zip(group_v, group_a))
        parts.append(f"{interleaved}concat=n={len(groups)}:v=1:a=1[vpre][apre]")
        v_pre, a_pre = "vpre", "apre"

    v_final: list[str] = []
    a_final: list[str] = []

    # Levelling sets clips by their programme loudness and ignores peaks, so a
    # boosted clip's transients could otherwise run past full scale. Catching
    # them here costs one filter and leaves everything below the limit alone --
    # far better than holding a whole clip down because it once got loud.
    if script.balance.enabled:
        a_final.append(f"alimiter=limit={OUTPUT_CEILING:.4f}:level=disabled")

    if in_fade > 0:
        v_final.append(f"fade=t=in:st=0:d={in_fade}")
        a_final.append(f"afade=t=in:st=0:d={in_fade}")
    if out_fade > 0:
        start = max(0.0, total - out_fade)
        v_final.append(f"fade=t=out:st={start:.3f}:d={out_fade}")
        a_final.append(f"afade=t=out:st={start:.3f}:d={out_fade}")

    if v_final:
        parts.append(f"[{v_pre}]{','.join(v_final)}[vout]")
        v_pre = "vout"
    if a_final:
        parts.append(f"[{a_pre}]{','.join(a_final)}[aout]")
        a_pre = "aout"

    return ";".join(parts), v_pre, a_pre


def format_time(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _run_with_progress(
    cmd: list[str], total_dur: float, on_progress=None, on_start=None, on_stderr=None
) -> None:
    """Run ffmpeg, reporting progress to a callback or the console."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if on_start:
        on_start(proc)

    # Drain stderr concurrently; ffmpeg can fill the pipe before stdout progress starts.
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            stderr_lines.append(line)
            if on_stderr:
                on_stderr(line.rstrip())

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
            if on_progress:
                on_progress(elapsed, total_dur, pct, speed)
                continue
            timing = f"{format_time(elapsed)} / {format_time(total_dur)} ({pct:.0f}%)"
            speed_str = f"  @ {speed}" if speed not in ("", "N/A") else ""
            print(f"\r  Rendering: {timing}{speed_str}   ", end="", flush=True)

    proc.wait()
    stderr_thread.join()
    if not on_progress:
        print()  # move past the in-place progress line

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, stderr="".join(stderr_lines)
        )


def probe_script(script: VideoScript, *, verbose: bool = True, on_step=None) -> list[ClipInfo]:
    """Probe every clip, running silence detection where the script asks for it.

    Returns one ClipInfo per clip of `expand_regions(script.clips)`, which is
    what render_script renders -- use that same list if you need to pair them.
    """
    def log(msg: str) -> None:
        if verbose:
            print(msg)

    infos: list[ClipInfo] = []
    # Keyed by source *and* the spans analysed: the same file cut two ways is
    # two different questions, and the answer to one is wrong for the other.
    cache: dict[tuple, list[tuple[float, float]] | None] = {}
    todo = expand_regions(script.clips)

    def step(done: int, message: str) -> None:
        if on_step:
            on_step(done, len(todo), message)

    for index, clip in enumerate(todo):
        step(index, f"reading {clip.path.name}")
        info = probe_clip(clip.path)
        keep = (
            _clamp([r.as_tuple() for r in clip.regions], info.duration)
            if clip.regions else None
        )

        if clip.trim_silence and info.has_audio:
            # Only the material the edit keeps is worth analysing -- see
            # compute_keep_intervals for why that is also more accurate.
            spans = keep or [(0.0, info.duration)]
            key = (clip.path, tuple(spans))
            if key not in cache:
                span_total = sum(b - a for a, b in spans)
                scope = (
                    f"{format_time(span_total)} of {format_time(info.duration)}"
                    if keep else format_time(info.duration)
                )
                log(f"    analysing silence: {clip.path.name} ({scope})")
                step(index, f"detecting silence in {clip.path.name}")
                cache[key] = compute_keep_intervals(
                    clip.path, info.duration, script.silence, within=spans
                )
            loud = cache[key]
            if loud:
                keep = _intersect(keep, loud) if keep else loud

        info.keep_intervals = keep or None

        if script.balance.enabled and info.has_audio:
            # A level written into the script is trusted; anything else is
            # measured now, over the spans the edit actually keeps.
            if clip.balance_db is not None:
                info.balance_db = clip.balance_db
            else:
                spans = info.keep_intervals or None
                key = ("balance", clip.path, tuple(spans) if spans else None)
                if key not in cache:
                    log(f"    measuring loudness: {clip.path.name}")
                    step(index, f"measuring loudness of {clip.path.name}")
                    measured = measure_loudness(clip.path, spans)
                    cache[key] = (
                        balance_gain(measured, script.balance.target_lufs)
                        if measured is not None and measured.usable else 0.0
                    )
                info.balance_db = cache[key]
                if info.balance_db:
                    log(f"      levelled {info.balance_db:+g} dB")

        if info.keep_intervals:
            removed = info.duration - info.effective_duration
            log(
                f"    {clip.path.name}: {len(info.keep_intervals)} segment(s) kept, "
                f"{format_time(removed)} trimmed "
                f"({format_time(info.duration)} -> "
                f"{format_time(info.effective_duration)})"
            )

        infos.append(info)
        step(index + 1, f"read {clip.path.name}")

    return infos


def render_script(
    script: VideoScript,
    infos: list[ClipInfo],
    *,
    verbose: bool = True,
    on_progress=None,
    on_start=None,
    on_stderr=None,
    on_stage=None,
) -> Path:
    """Render the script in one ffmpeg pass, writing no intermediate files."""
    output_path = script.output.file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clips = expand_regions(script.clips)
    groups = _split_into_groups(clips)

    if verbose and not on_progress:
        print(f"  Building filter graph ({len(clips)} clips, {len(groups)} group(s))...")
    if on_stage:
        on_stage("graph", f"{len(clips)} clips in {len(groups)} group(s)")

    filter_complex, v_out, a_out = _build_filter_complex(infos, clips, groups, script)

    # -progress and -nostats are global options and must precede all -i flags.
    cmd = ["ffmpeg", "-hide_banner", "-y", "-progress", "pipe:1", "-nostats"]
    for clip in clips:
        cmd += ["-i", str(clip.path)]

    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{v_out}]",
        "-map", f"[{a_out}]",
        *_encode_args(script.output.encoder, script.output.quality),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(output_path),
    ]

    if on_stage:
        on_stage("encode", f"{script.output.encoder} -> {output_path.name}")

    if verbose or on_progress:
        _run_with_progress(
            cmd, _total_duration(groups, infos, clips), on_progress, on_start, on_stderr
        )
    else:
        subprocess.run(cmd, check=True, capture_output=True)

    return output_path
