"""Parse and format timecodes like `90`, `1:30`, `1:02:03.5`."""

import re

_TIMECODE_RE = re.compile(r"^\d+(?::[0-5]?\d){0,2}(?:\.\d+)?$")


def parse_timecode(text: str) -> float:
    """Seconds from `SS`, `MM:SS` or `HH:MM:SS`, with optional decimals."""
    text = text.strip()
    if not _TIMECODE_RE.match(text):
        raise ValueError(f"bad timecode {text!r}")
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def format_timecode(seconds: float) -> str:
    """Render seconds as `M:SS.mmm`, or `H:MM:SS.mmm` past an hour."""
    seconds = max(0.0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    text = f"{int(hours)}:{int(minutes):02d}:{secs:06.3f}" if hours else f"{int(minutes)}:{secs:06.3f}"
    return text.rstrip("0").rstrip(".") if "." in text else text
