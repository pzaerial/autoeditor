"""What ffprobe says about a clip, and what the edit keeps of it.

`probe_script` is the step between a parsed script and a render: it resolves
every clip to a `ClipInfo`, works out which parts of each source survive the
edit, and -- where the script asks for them -- runs the analyses that decide
that. Everything downstream reads those `ClipInfo`s rather than the files.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .loudness import balance_gain, measure_loudness
from .silence import compute_keep_intervals
from .timecode import format_time
from .timeline import VideoScript, expand_regions


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



def _clamp(intervals, duration: float) -> list[tuple[float, float]]:
    """Trim intervals to the clip's real length, dropping any that fall outside."""
    out = []
    for a, b in intervals:
        a, b = max(0.0, a), min(duration, b)
        if b - a > 1e-6:
            out.append((a, b))
    return out


def _drop_head(intervals, seconds: float) -> list[tuple[float, float]]:
    """Advance past the first `seconds` of kept material."""
    out = []
    left = seconds
    for a, b in intervals:
        span = b - a
        if left >= span - 1e-6:
            left -= span
            continue
        out.append((a + left, b))
        left = 0.0
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

        # An `audio overlap` join drops the head of this clip's *picture*; the
        # sound it belonged to is taken back in audio_layout, which is what
        # keeps the incoming clip's own sound and picture locked together.
        if clip.overlap > 0:
            keep = _drop_head(keep or [(0.0, info.duration)], clip.overlap)

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


