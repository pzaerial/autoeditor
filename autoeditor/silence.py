"""Dead-space detection -- find the loud (keep) regions of a recording."""

import re
import subprocess
from pathlib import Path

from .timeline import SilenceSettings


_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[0-9.]+)")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?[0-9.]+)\s*dB")


def _run_audio_filter(path: Path, audio_filter: str) -> str:
    """Run an audio-only ffmpeg pass and return its stderr text."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", str(path),
            "-vn", "-af", audio_filter,
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    return result.stderr


def _measure_peak_db(path: Path) -> float | None:
    """Return the clip's peak loudness in dB, or None if unknown."""
    stderr = _run_audio_filter(path, "volumedetect")
    match = _MAX_VOLUME_RE.search(stderr)
    return float(match.group(1)) if match else None


def _detect_silences(
    path: Path, noise_db: float, min_silence: float, duration: float
) -> list[tuple[float, float]]:
    """Return silent (start, end) intervals as detected by silencedetect."""
    audio_filter = f"silencedetect=noise={noise_db:.1f}dB:d={min_silence}"
    stderr = _run_audio_filter(path, audio_filter)

    silences: list[tuple[float, float]] = []
    pending_start: float | None = None

    for line in stderr.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            pending_start = float(start_match.group(1))
            continue
        end_match = _SILENCE_END_RE.search(line)
        if end_match and pending_start is not None:
            silences.append((max(0.0, pending_start), float(end_match.group(1))))
            pending_start = None

    # A silence running to the end of the file emits no silence_end.
    if pending_start is not None:
        silences.append((max(0.0, pending_start), duration))

    return silences


def _invert(silences: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Complement of the silent intervals within [0, duration] = loud regions."""
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in silences:
        if s_start > cursor:
            keep.append((cursor, s_start))
        cursor = max(cursor, s_end)
    if cursor < duration:
        keep.append((cursor, duration))
    return keep


def _pad_and_merge(
    keep: list[tuple[float, float]], padding: float, duration: float
) -> list[tuple[float, float]]:
    """Pad each keep region and merge any that overlap, avoiding micro-cuts."""
    padded = [
        (max(0.0, a - padding), min(duration, b + padding)) for a, b in keep
    ]
    merged: list[tuple[float, float]] = []
    for start, end in padded:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def compute_keep_intervals(
    path: Path, duration: float, settings: SilenceSettings
) -> list[tuple[float, float]] | None:
    """Loud (keep) intervals for a recording; None means keep the whole clip."""
    peak_db = _measure_peak_db(path)
    if peak_db is None:
        return None

    # Floor relative to the clip's own peak, so it adapts to varying mic gain.
    noise_db = peak_db + settings.threshold_db

    silences = _detect_silences(path, noise_db, settings.min_silence, duration)
    if not silences:
        return None

    keep = _invert(silences, duration)
    keep = _pad_and_merge(keep, settings.padding, duration)
    keep = [(a, b) for a, b in keep if b - a >= settings.min_segment]

    if not keep:
        return None
    if len(keep) == 1 and keep[0][0] <= 0.0 and keep[0][1] >= duration:
        return None

    return keep
