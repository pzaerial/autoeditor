"""Data model for a parsed markdown video script."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".wmv", ".flv"}


class Join(Enum):
    """How a clip attaches to the one before it."""

    CUT = "cut"
    CROSSFADE = "crossfade"
    FADE = "fade"


@dataclass
class OutputSettings:
    file: Path
    resolution: str = "1920x1080"
    fps: int = 60
    encoder: str = "libx264"
    # CRF-like: lower is better and bigger. None takes the encoder's default.
    quality: float | None = None
    dry_run: bool = False

    @property
    def size(self) -> tuple[int, int]:
        w, h = self.resolution.lower().replace("×", "x").split("x")
        return int(w.strip()), int(h.strip())


@dataclass
class SilenceSettings:
    """Dead-space detection tuning, applied per clip that opts in."""

    threshold_db: float = -30.0
    padding: float = 0.5
    min_silence: float = 1.0
    min_segment: float = 0.5


@dataclass
class BalanceSettings:
    """Level every clip to the same loudness. Opt-in, like silence trimming."""

    enabled: bool = False
    # EBU R128 integrated loudness. -14 LUFS is what YouTube normalises to.
    target_lufs: float = -14.0


@dataclass
class Defaults:
    """How clips are joined: to each other, and to the black either end."""

    join: Join = Join.CROSSFADE
    crossfade: float = 0.3
    fade: float = 0.5
    # Up from black at the very start, down to black at the very end.
    fade_in: float = 0.5
    fade_out: float = 0.5
    trim_silence: bool = False
    # How a join's sound is handled when the item does not say. None means the
    # audio transition simply matches the picture's.
    audio_overlap: float | None = None
    audio_lead: float = 0.0


@dataclass
class Region:
    """A kept range of a clip's source, and how it joins the range before it."""

    start: float
    end: float
    join: Join = Join.CUT
    join_duration: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_tuple(self) -> tuple[float, float]:
        return (self.start, self.end)


@dataclass
class TimelineClip:
    path: Path
    label: str
    join: Join
    join_duration: float
    trim_silence: bool
    line: int = 0
    # This clip's own audio trim, added to the project-wide gain.
    audio_gain_db: float = 0.0
    # What levelling worked out for this clip, kept apart from the manual trim
    # so the two never fight and neither is applied twice. None means it has
    # not been measured yet; the render measures it when balancing is on.
    balance_db: float | None = None
    # How long this join's audio transition runs. None follows the picture's
    # join_duration, which is what makes a plain crossfade sound like one.
    audio_overlap: float | None = None
    # Seconds the audio transition happens *before* the picture cut. Positive
    # is a J-cut (the next clip is heard first), negative an L-cut (the last
    # clip is still heard over the new picture).
    audio_lead: float = 0.0
    # Kept ranges of the source; None means the whole clip.
    regions: list[Region] | None = None
    # True when the file was absent at load time (non-strict parsing only).
    missing: bool = False

    @property
    def audio_blend(self) -> float:
        """Length of this join's audio transition."""
        return self.join_duration if self.audio_overlap is None else self.audio_overlap

    def audio_follows_picture(self) -> bool:
        """True when this join's sound needs no timeline of its own."""
        return not self.audio_lead and abs(self.audio_blend - self.join_duration) < 1e-9

    def needs_region_split(self) -> bool:
        """True when regions blend into each other and cannot be plain-concatenated."""
        return bool(self.regions) and any(r.join is not Join.CUT for r in self.regions[1:])


@dataclass
class VideoScript:
    source: Path
    title: str
    output: OutputSettings
    silence: SilenceSettings
    defaults: Defaults
    balance: BalanceSettings = field(default_factory=BalanceSettings)
    clips: list[TimelineClip] = field(default_factory=list)

    def describe(self) -> str:
        """Rundown of the edit, printed before rendering."""
        width = max((len(c.label) for c in self.clips), default=0)
        lines = []
        for i, clip in enumerate(self.clips, 1):
            if i == 1:
                join = ""
            elif clip.join is Join.CUT:
                join = "cut"
            else:
                join = f"{clip.join.value} {clip.join_duration:g}s"
            flags = "  [trim silence]" if clip.trim_silence else ""
            if clip.audio_gain_db:
                flags += f"  [{clip.audio_gain_db:+g} dB]"
            if clip.balance_db:
                flags += f"  [balance {clip.balance_db:+g} dB]"
            if i > 1 and not clip.audio_follows_picture():
                flags += f"  [audio {clip.audio_blend:g}s @ {clip.audio_lead:+g}s]"
            if clip.regions:
                flags += f"  [{len(clip.regions)} region(s)]"
            lines.append(
                f"  {i:>3}. {clip.label:<{width}}  {join:<14}{flags}"
                f"\n       {clip.path}"
            )
        return "\n".join(lines)
