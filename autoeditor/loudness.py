"""Loudness measurement, for levelling clips against each other.

Recordings from different sessions rarely match: mic gain drifts, a capture card
runs hot, an asset was mastered somewhere else. Matching them by ear one clip at
a time is the tedious part of assembling an episode, and it is exactly what a
loudness measurement does properly.

The measure is EBU R128 loudness (LUFS), but *not* the standard integrated
figure -- see `_typical_loudness` for why that one cannot be trusted on this
material.
"""

import re
import statistics
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

# What YouTube normalises to. Quieter than broadcast, louder than film.
DEFAULT_TARGET_LUFS = -14.0
# Nothing sensible needs more than this, and a wild figure means a bad measure.
MAX_GAIN_DB = 24.0
# Below this a block is silence, not quiet programme. R128's absolute gate.
ABSOLUTE_GATE_LUFS = -70.0
# How far below the reference a block can be and still count as programme.
RELATIVE_GATE_LU = 10.0
# The reference the relative gate hangs off. A high percentile rather than the
# mean, which is what makes it immune to a single loud moment.
REFERENCE_PERCENTILE = 0.90

_MOMENTARY_RE = re.compile(r"lavfi\.r128\.M=(-?[\d.]+)")
_PTS_RE = re.compile(r"pts_time:([\d.]+)")
_PEAK_RE = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+)\s*dBFS", re.M)


@dataclass
class Loudness:
    """What one clip measures, over the material the edit actually keeps."""

    lufs: float       # typical programme loudness
    peak_dbtp: float  # true peak

    @property
    def usable(self) -> bool:
        # Digital silence reports -inf (or close enough); nothing to balance.
        return self.lufs > ABSOLUTE_GATE_LUFS


def _spans_filter(spans: list[tuple[float, float]]) -> str:
    """Concatenate the kept spans so one pass measures exactly that material."""
    count = len(spans)
    outs = "".join(f"[s{i}]" for i in range(count))
    parts = [f"[0:a]asplit={count}{outs}"]
    trimmed = []
    for i, (start, end) in enumerate(spans):
        parts.append(
            f"[s{i}]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[t{i}]"
        )
        trimmed.append(f"[t{i}]")
    parts.append(f"{''.join(trimmed)}concat=n={count}:v=0:a=1[kept]")
    return ";".join(parts)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _typical_loudness(blocks: list[float]) -> float | None:
    """Programme loudness from 400 ms blocks, robust to loud moments.

    R128's *integrated* loudness gates at 10 LU below the ungated mean, and that
    mean is not robust: one very loud moment drags it up until the whole quiet
    body of a recording falls below the gate and is thrown away. Measured on a
    two-minute quiet recording, adding a single 0.2 s full-scale transient moved
    the integrated figure from -55.9 to -8.1 LUFS -- a 48 dB error that turned a
    clip needing a large boost into one the levelling wanted to pull *down*.
    Game audio and stream captures are full of such moments.

    Two changes, both for the same reason. The gate hangs off a high percentile
    of the blocks rather than their mean, so one loud moment cannot move it. And
    what survives is combined with a *median* rather than R128's energy mean,
    which is likewise dominated by its loudest members -- six full-scale blocks
    among twelve hundred quiet ones still pulled the energy mean up by 25 dB.

    The median of the gated blocks is "the level this clip sits at most of the
    time", which is what makes two clips sound alike. On well-behaved material
    it lands within 0.1 dB of the standard integrated figure; on spiky material
    it stays where the programme actually is.
    """
    speaking = [b for b in blocks if b > ABSOLUTE_GATE_LUFS]
    if not speaking:
        return None
    reference = _percentile(speaking, REFERENCE_PERCENTILE)
    kept = [b for b in speaking if b >= reference - RELATIVE_GATE_LU]
    return statistics.median(kept or speaking)


def measure_loudness(
    path: Path,
    within: list[tuple[float, float]] | None = None,
    *,
    on_progress=None,
    on_start=None,
) -> Loudness | None:
    """Typical loudness and true peak, or None if it cannot be measured.

    `within` restricts the measurement to the spans the edit keeps, joined into
    one stream so the blocks come from exactly the material being used.

    Every block printed carries its own `pts_time`, so `on_progress(seconds)`
    can report how far in the pass has reached without asking ffmpeg for
    anything extra. `on_start(proc)` hands over the process so a caller can
    cancel a long measurement.
    """
    analysis = (
        "ebur128=peak=true:metadata=1,"
        "ametadata=mode=print:key=lavfi.r128.M:file=-"
    )
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn"]
    if within:
        cmd += [
            "-filter_complex", f"{_spans_filter(within)};[kept]{analysis}[out]",
            "-map", "[out]",
        ]
    else:
        cmd += ["-af", analysis]
    cmd += ["-f", "null", "-"]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    except OSError:
        return None
    if on_start:
        on_start(proc)

    # The summary lands on stderr at the very end; drain it so a long pass
    # cannot fill the pipe and stall.
    tail: list[str] = []

    def _drain() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            tail.append(line)

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    blocks: list[float] = []
    reported = -1.0
    for line in proc.stdout:  # type: ignore[union-attr]
        found = _MOMENTARY_RE.search(line)
        if found:
            blocks.append(float(found.group(1)))
            continue
        if on_progress:
            at = _PTS_RE.search(line)
            # A block every 100 ms is far more often than anyone needs told.
            if at and float(at.group(1)) - reported >= 0.5:
                reported = float(at.group(1))
                on_progress(reported)

    proc.wait()
    reader.join()

    loudness = _typical_loudness(blocks)
    if loudness is None:
        return None

    peak = _PEAK_RE.search("".join(tail))
    return Loudness(round(loudness, 2), float(peak.group(1)) if peak else 0.0)


def balance_gain(
    measured: Loudness, target_lufs: float = DEFAULT_TARGET_LUFS
) -> float:
    """The dB trim that puts this clip's programme level on the target.

    A straight gain, not compression: the clip keeps its own dynamics and only
    its level moves, which is what balancing clips against each other means.

    Peaks are deliberately not consulted. Holding a clip down because it once
    got loud is how one transient ends up deciding the level of a two-hour
    recording; loud peaks are fine, and the render puts a limiter on the output
    instead so they cannot clip.
    """
    gain = target_lufs - measured.lufs
    return round(max(-MAX_GAIN_DB, min(MAX_GAIN_DB, gain)), 1)
