"""Dead-space detection -- find the loud (keep) regions of a recording."""

import re
import subprocess
from pathlib import Path

from .timeline import SilenceSettings


_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[0-9.]+)")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?[0-9.]+)\s*dB")


def _run_audio_filter(
    path: Path, audio_filter: str, start: float = 0.0, length: float | None = None
) -> str:
    """Run an audio-only ffmpeg pass over one span and return its stderr text.

    `-ss` before `-i` seeks the input rather than decoding up to the mark, so
    analysing a section near the end of a long recording costs what the section
    costs. Timestamps in the output are then relative to that seek.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-vn", "-af", audio_filter, "-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def _measure_peak_db(path: Path, start: float, length: float) -> float | None:
    """Peak loudness in dB over one span, or None if unknown."""
    stderr = _run_audio_filter(path, "volumedetect", start, length)
    match = _MAX_VOLUME_RE.search(stderr)
    return float(match.group(1)) if match else None


def _detect_silences(
    path: Path, noise_db: float, min_silence: float, start: float, end: float
) -> list[tuple[float, float]]:
    """Silent (start, end) intervals within one span, in source time."""
    audio_filter = f"silencedetect=noise={noise_db:.1f}dB:d={min_silence}"
    stderr = _run_audio_filter(path, audio_filter, start, end - start)

    silences: list[tuple[float, float]] = []
    pending: float | None = None

    for line in stderr.splitlines():
        opened = _SILENCE_START_RE.search(line)
        if opened:
            pending = float(opened.group(1))
            continue
        closed = _SILENCE_END_RE.search(line)
        if closed and pending is not None:
            silences.append((start + max(0.0, pending), start + float(closed.group(1))))
            pending = None

    # A silence running to the end of the span emits no silence_end.
    if pending is not None:
        silences.append((start + max(0.0, pending), end))

    return [(max(start, a), min(end, b)) for a, b in silences]


def _invert(
    silences: list[tuple[float, float]], start: float, end: float
) -> list[tuple[float, float]]:
    """Complement of the silent intervals within [start, end] = loud regions."""
    keep: list[tuple[float, float]] = []
    cursor = start
    for s_start, s_end in silences:
        if s_start > cursor:
            keep.append((cursor, s_start))
        cursor = max(cursor, s_end)
    if cursor < end:
        keep.append((cursor, end))
    return keep


def _pad_and_merge(
    keep: list[tuple[float, float]], padding: float, low: float, high: float
) -> list[tuple[float, float]]:
    """Pad each keep region and merge any that overlap, avoiding micro-cuts.

    Padding is clamped to the span being analysed: a region you chose is the
    limit of what may be kept, so breathing room cannot reach back into
    material you already cut.
    """
    padded = [
        (max(low, a - padding), min(high, b + padding)) for a, b in keep
    ]
    merged: list[tuple[float, float]] = []
    for start, end in padded:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def compute_keep_intervals(
    path: Path,
    duration: float,
    settings: SilenceSettings,
    within: list[tuple[float, float]] | None = None,
) -> list[tuple[float, float]] | None:
    """Loud (keep) intervals for a recording; None means keep it all.

    `within` limits the analysis to the spans the edit actually uses. That is
    not only faster -- two audio decodes of a four-hour stream to keep twenty
    minutes of it is most of the wait before a render starts -- it is also more
    accurate, because the noise floor is set relative to the peak of the
    material being kept rather than to a loud moment in footage already cut.
    """
    spans = [(a, b) for a, b in (within or [(0.0, duration)]) if b - a > 1e-6]
    if not spans:
        return None

    peaks = [p for p in (_measure_peak_db(path, a, b - a) for a, b in spans) if p is not None]
    if not peaks:
        return None

    # Floor relative to the kept material's own peak, so it adapts to varying
    # mic gain between sessions.
    noise_db = max(peaks) + settings.threshold_db

    keep: list[tuple[float, float]] = []
    for start, end in spans:
        silences = _detect_silences(path, noise_db, settings.min_silence, start, end)
        loud = _invert(silences, start, end)
        keep += _pad_and_merge(loud, settings.padding, start, end)

    keep = [(a, b) for a, b in keep if b - a >= settings.min_segment]
    if not keep:
        return None

    # Nothing was actually removed, so let the caller skip the trimming machinery.
    analysed = sum(b - a for a, b in spans)
    if abs(sum(b - a for a, b in keep) - analysed) < 1e-6:
        return None

    return keep
