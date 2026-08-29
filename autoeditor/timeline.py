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
    fade_in: float = 0.5
    fade_out: float = 0.5
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
class Defaults:
    """Fallbacks for timeline items that do not state their own."""

    join: Join = Join.CROSSFADE
    crossfade: float = 0.3
    fade: float = 0.5
    trim_silence: bool = False


@dataclass
class TimelineClip:
    path: Path
    label: str
    join: Join
    join_duration: float
    trim_silence: bool
    line: int = 0


@dataclass
class VideoScript:
    source: Path
    title: str
    output: OutputSettings
    silence: SilenceSettings
    defaults: Defaults
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
            lines.append(
                f"  {i:>3}. {clip.label:<{width}}  {join:<14}{flags}"
                f"\n       {clip.path}"
            )
        return "\n".join(lines)
