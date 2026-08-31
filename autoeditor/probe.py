"""What ffprobe says about a clip, and what the edit keeps of it.

`probe_project` is the step between a parsed timeline and a render: it resolves
every clip to a `ClipInfo`, works out which parts of each source survive the
edit, and -- where the script asks for them -- runs the analyses that decide
that. Everything downstream reads those `ClipInfo`s rather than the files.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .silence import compute_keep_intervals
from .timecode import format_time
from .tracks import TrackKind


@dataclass
class ClipInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    # False for a source with no picture at all -- a music bed on an audio
    # track. Everything downstream that draws has to ask, because such a clip
    # contributes sound and nothing else.
    has_video: bool = True
    vcodec: str = ""
    acodec: str = ""
    pix_fmt: str = ""
    # Loud regions to keep; None means the clip plays in full.
    keep_intervals: list[tuple[float, float]] | None = None

    @property
    def effective_duration(self) -> float:
        """Output duration after dead-space trimming."""
        if self.keep_intervals is None:
            return self.duration
        return sum(b - a for a, b in self.keep_intervals)



def probe_clip(path: Path, *, allow_audio_only: bool = False) -> ClipInfo:
    """What ffprobe says about one file.

    A file with no video stream is an error by default, because everywhere the
    app offers you a clip it means a clip you can see. An audio track's music
    bed is the exception, and asks for it.
    """
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
    if video_stream is None and not allow_audio_only:
        raise ValueError(f"No video stream in {path}")

    audio_streams = [s for s in data["streams"] if s["codec_type"] == "audio"]
    if video_stream is None and not audio_streams:
        raise ValueError(f"No video or audio stream in {path}")

    fps = 0.0
    if video_stream is not None:
        fps_num, fps_den = video_stream.get("r_frame_rate", "30/1").split("/")
        fps = int(fps_num) / int(fps_den) if int(fps_den) else 0.0

    return ClipInfo(
        path=path,
        duration=float(data["format"].get("duration", 0)),
        width=int(video_stream["width"]) if video_stream else 0,
        height=int(video_stream["height"]) if video_stream else 0,
        fps=fps,
        has_audio=bool(audio_streams),
        has_video=video_stream is not None,
        vcodec=video_stream.get("codec_name", "") if video_stream else "",
        acodec=audio_streams[0].get("codec_name", "") if audio_streams else "",
        pix_fmt=video_stream.get("pix_fmt", "") if video_stream else "",
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



def probe_project(project, *, verbose: bool = True, on_step=None) -> list[ClipInfo]:
    """Probe every clip of a track timeline, in `project.all_clips()` order.

    The same three questions `probe_script` asks -- what range is kept, what of
    it is not silence, and what the transition before it takes away -- asked of
    a clip whose range and transition come from its track rather than from
    fields on itself. That order is also the ffmpeg input order, so a clip's
    position in this list is its input index.
    """
    def log(msg: str) -> None:
        if verbose:
            print(msg)

    pairs = project.all_clips()
    infos: list[ClipInfo] = []
    cache: dict[tuple, list[tuple[float, float]] | None] = {}

    def step(done: int, message: str) -> None:
        if on_step:
            on_step(done, len(pairs), message)

    position = 0
    for track in project.tracks:
        for index, clip in enumerate(track.clips()):
            step(position, f"reading {clip.source.name}")
            info = probe_clip(
                clip.source, allow_audio_only=track.kind is TrackKind.AUDIO
            )

            # The chosen range of the source, if the clip narrows it.
            end = info.duration if clip.source_out is None else clip.source_out
            keep = None
            if clip.source_in > 0 or clip.source_out is not None:
                keep = _clamp([(clip.source_in, end)], info.duration)

            if clip.trim_silence and info.has_audio:
                spans = keep or [(0.0, info.duration)]
                key = (clip.source, tuple(spans))
                if key not in cache:
                    span_total = sum(b - a for a, b in spans)
                    scope = (
                        f"{format_time(span_total)} of {format_time(info.duration)}"
                        if keep else format_time(info.duration)
                    )
                    log(f"    analysing silence: {clip.source.name} ({scope})")
                    step(position, f"detecting silence in {clip.source.name}")
                    cache[key] = compute_keep_intervals(
                        clip.source, info.duration, project.silence, within=spans
                    )
                loud = cache[key]
                if loud:
                    keep = _intersect(keep, loud) if keep else loud

            # A transition like `audio overlap` takes the head off this clip's
            # picture. Its sound reaches back into exactly what was given up,
            # which is what keeps the two locked to each other.
            before = track.transition_before(index)
            trim = before.head_trim if before else 0.0
            if trim > 0:
                keep = _drop_head(keep or [(0.0, info.duration)], trim)

            info.keep_intervals = keep or None

            if info.keep_intervals:
                removed = info.duration - info.effective_duration
                log(
                    f"    {clip.source.name}: {len(info.keep_intervals)} segment(s) "
                    f"kept, {format_time(removed)} trimmed "
                    f"({format_time(info.duration)} -> "
                    f"{format_time(info.effective_duration)})"
                )

            infos.append(info)
            position += 1
            step(position, f"read {clip.source.name}")

    if project.balance.enabled and not any(c.gain_db for _, c in pairs):
        log(
            "    note: levelling is on but no clip has a level yet -- "
            "measure them in the app, or set `volume` on each item"
        )

    return infos
