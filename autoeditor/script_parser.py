"""Parse a markdown video script into a VideoScript."""

import os
import re
from pathlib import Path

from .timeline import (
    VIDEO_EXTENSIONS,
    Defaults,
    Join,
    OutputSettings,
    SilenceSettings,
    TimelineClip,
    VideoScript,
)


class ScriptError(Exception):
    """A problem in the script file, reported with a line number."""

    def __init__(self, message: str, line: int | None = None):
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_RESOLUTION_RE = re.compile(r"^\d+\s*[x\u00d7]\s*\d+$")
_OPTION_SPLIT_RE = re.compile(r"\s(?:--|\u2014|\u2013|\|)\s")

_SECTION_ALIASES = {
    "output": "output",
    "output settings": "output",
    "settings": "output",
    "defaults": "defaults",
    "default": "defaults",
    "silence": "silence",
    "trim silence": "silence",
    "dead space": "silence",
    "dead space removal": "silence",
    "assets": "assets",
    "files": "assets",
    "sources": "assets",
    "timeline": "timeline",
    "sequence": "timeline",
    "order": "timeline",
}

_TRUTHY = {"true", "yes", "on", "1", "y"}
_FALSY = {"false", "no", "off", "0", "n"}


def _norm_key(text: str) -> str:
    """Normalise a key so `Fade In`, `fade_in` and `**fade-in**` all match."""
    text = text.strip().strip("*`_ ").strip()
    text = text.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).lower()


def _clean_value(text: str) -> str:
    """Strip markdown decoration and quoting from a value."""
    for _ in range(2):
        text = text.strip()
        if text.startswith("**") and text.endswith("**") and len(text) > 4:
            text = text[2:-2]
        elif len(text) >= 2 and text[0] == text[-1] and text[0] in ("`", '"', "'"):
            text = text[1:-1]
    return text.strip()


def _split_kv(item: str, line: int, section: str) -> tuple[str, str]:
    """Split `key: value`, tolerating the colon inside `C:\\path`."""
    match = re.match(r"^([^:]{1,60}):\s*(.*)$", item)
    if not match:
        raise ScriptError(
            f"expected `key: value` in the {section} section, got {item!r}", line
        )

    key, value = _norm_key(match.group(1)), _clean_value(match.group(2))
    # A bare `C:\...` path would otherwise register a setting named after the drive.
    if len(key) == 1 and value[:1] in ("\\", "/"):
        raise ScriptError(
            f"{section} entries need a name: `name: {match.group(0).strip()}`", line
        )
    return key, value


def _parse_bool(value: str, key: str, line: int) -> bool:
    low = value.strip().lower()
    if low in _TRUTHY:
        return True
    if low in _FALSY:
        return False
    raise ScriptError(f"{key}: expected yes/no, got {value!r}", line)


def _parse_number(value: str, key: str, line: int) -> float:
    """Parse a duration or level, ignoring a trailing unit (`0.5s`, `-30 dB`)."""
    cleaned = re.sub(r"(?i)\s*(seconds|second|secs|sec|s|db)\s*$", "", value.strip())
    try:
        return float(cleaned)
    except ValueError:
        raise ScriptError(f"{key}: expected a number, got {value!r}", line) from None


def _unknown_key(key: str, valid: dict, section: str, line: int) -> ScriptError:
    options = ", ".join(sorted(valid))
    return ScriptError(f"unknown {section} setting {key!r}. Valid: {options}", line)


def _resolve_path(raw: str, base: Path) -> Path:
    """Expand env vars and `~`, then anchor relative paths to the script dir."""
    expanded = os.path.expandvars(os.path.expanduser(raw.strip().strip('"')))
    path = Path(expanded)
    return path if path.is_absolute() else (base / path)


_WINDOWS_RESERVED = '<>:"|?*'


def _check_writable_name(path: Path, line: int) -> None:
    """Reject an output path Windows would silently write as a data stream."""
    if os.name != "nt":
        return
    for part in path.parts[1:]:
        bad = sorted({ch for ch in part if ch in _WINDOWS_RESERVED})
        if bad:
            raise ScriptError(
                f"output path contains {' '.join(bad)} in {part!r}, which "
                f"Windows cannot use in a file or folder name. Try a dash: "
                f"{part.translate({ord(c): '-' for c in _WINDOWS_RESERVED})!r}",
                line,
            )


def _expand_source(path: Path, line: int) -> list[Path]:
    """Expand a directory or glob into its video files, oldest first."""
    if any(ch in path.name for ch in "*?[") and not path.exists():
        matches = [p for p in path.parent.glob(path.name) if p.is_file()]
        if not matches:
            raise ScriptError(f"no files match {path}", line)
        return sorted(matches, key=lambda p: (p.stat().st_mtime, p.name))

    if path.is_dir():
        videos = [
            f for f in path.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        ]
        if not videos:
            raise ScriptError(f"no video files in folder {path}", line)
        return sorted(videos, key=lambda f: (f.stat().st_mtime, f.name))

    if not path.exists():
        raise ScriptError(f"file not found: {path}", line)
    return [path]


_JOIN_WORDS = {
    "cut": Join.CUT,
    "hard cut": Join.CUT,
    "crossfade": Join.CROSSFADE,
    "dissolve": Join.CROSSFADE,
    "fade": Join.FADE,
}

_JOIN_OPTION_RE = re.compile(r"^(hard cut|cut|crossfade|dissolve|fade)\s*(.*)$")


def _parse_options(text: str, line: int) -> tuple[Join | None, float | None, bool | None]:
    """Parse the comma-separated options after a timeline item's separator."""
    join: Join | None = None
    duration: float | None = None
    trim: bool | None = None

    for raw_option in text.split(","):
        option = _norm_key(raw_option)
        if not option:
            continue

        match = _JOIN_OPTION_RE.match(option)
        if match:
            name, rest = match.group(1), match.group(2).strip()
            join = _JOIN_WORDS[name]
            if rest:
                duration = _parse_number(rest, name, line)
            continue

        if option in ("trim silence", "trim", "remove silence", "remove dead space"):
            trim = True
        elif option in ("keep silence", "no trim", "no trim silence"):
            trim = False
        else:
            raise ScriptError(
                f"unknown timeline option {raw_option.strip()!r}. Valid: cut, "
                "crossfade [seconds], fade [seconds], trim silence, keep silence",
                line,
            )

    return join, duration, trim


def _default_duration(join: Join, defaults: Defaults) -> float:
    if join is Join.CROSSFADE:
        return defaults.crossfade
    if join is Join.FADE:
        return defaults.fade
    return 0.0


_OUTPUT_KEYS = {
    "file": "file", "output": "file", "path": "file", "output file": "file",
    "resolution": "resolution", "size": "resolution",
    "fps": "fps", "frame rate": "fps", "framerate": "fps",
    "encoder": "encoder", "video encoder": "encoder", "codec": "encoder",
    "fade in": "fade_in",
    "fade out": "fade_out",
    "dry run": "dry_run",
}

_SILENCE_KEYS = {
    "threshold": "threshold_db", "threshold db": "threshold_db",
    "padding": "padding", "pad": "padding",
    "min silence": "min_silence", "minimum silence": "min_silence",
    "min segment": "min_segment", "minimum segment": "min_segment",
}

_DEFAULT_KEYS = {
    "join": "join", "transition": "join",
    "crossfade": "crossfade",
    "fade": "fade",
    "trim silence": "trim_silence", "trim": "trim_silence",
}


def _apply_output(raw: dict[str, tuple[str, int]], base: Path) -> OutputSettings:
    if "file" not in raw:
        raise ScriptError("the Output section must set `file:` (the .mp4 to write)")

    out = OutputSettings(file=_resolve_path(raw["file"][0], base))
    _check_writable_name(out.file, raw["file"][1])

    for field_name, (value, line) in raw.items():
        if field_name == "file":
            continue
        if field_name == "resolution":
            if not _RESOLUTION_RE.match(value):
                raise ScriptError(f"resolution: expected WxH, got {value!r}", line)
            out.resolution = value.lower().replace("\u00d7", "x").replace(" ", "")
        elif field_name == "fps":
            out.fps = int(_parse_number(value, "fps", line))
        elif field_name == "encoder":
            out.encoder = value
        elif field_name == "dry_run":
            out.dry_run = _parse_bool(value, "dry run", line)
        else:
            setattr(out, field_name, _parse_number(value, field_name, line))

    return out


def _apply_silence(raw: dict[str, tuple[str, int]]) -> SilenceSettings:
    settings = SilenceSettings()
    for field_name, (value, line) in raw.items():
        setattr(settings, field_name, _parse_number(value, field_name, line))
    return settings


def _apply_defaults(raw: dict[str, tuple[str, int]]) -> Defaults:
    defaults = Defaults()
    for field_name, (value, line) in raw.items():
        if field_name == "join":
            key = _norm_key(value)
            if key not in _JOIN_WORDS:
                raise ScriptError(
                    f"join: expected cut, crossfade or fade, got {value!r}", line
                )
            defaults.join = _JOIN_WORDS[key]
        elif field_name == "trim_silence":
            defaults.trim_silence = _parse_bool(value, "trim silence", line)
        else:
            setattr(defaults, field_name, _parse_number(value, field_name, line))
    return defaults


def parse_script(path: Path) -> VideoScript:
    """Parse a markdown video script into a `VideoScript`."""
    if not path.exists():
        raise ScriptError(f"script file not found: {path}")

    base = path.resolve().parent
    text = path.read_text(encoding="utf-8-sig")

    title = path.stem
    section: str | None = None
    in_fence = False
    warnings: list[str] = []

    raw_output: dict[str, tuple[str, int]] = {}
    raw_silence: dict[str, tuple[str, int]] = {}
    raw_defaults: dict[str, tuple[str, int]] = {}
    assets: dict[str, str] = {}
    timeline: list[tuple[str, int]] = []
    saw_timeline = False

    section_tables = {
        "output": (_OUTPUT_KEYS, raw_output),
        "silence": (_SILENCE_KEYS, raw_silence),
        "defaults": (_DEFAULT_KEYS, raw_defaults),
    }

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.rstrip()

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip():
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            name = _norm_key(heading.group(2))
            # A top-level `# Heading` that is not a section name is the title.
            if len(heading.group(1)) == 1 and name not in _SECTION_ALIASES:
                title = heading.group(2).strip()
                section = None
                continue
            section = _SECTION_ALIASES.get(name)
            if section is None:
                warnings.append(f"line {lineno}: ignoring unknown section '{name}'")
            elif section == "timeline":
                saw_timeline = True
            continue

        item = _LIST_ITEM_RE.match(line)
        if not item or section is None:
            continue
        content = item.group(1).strip()
        if not content:
            continue

        if section == "timeline":
            timeline.append((content, lineno))
        elif section == "assets":
            key, value = _split_kv(content, lineno, "Assets")
            assets[key] = value
        else:
            key, value = _split_kv(content, lineno, section.capitalize())
            table, target = section_tables[section]
            if key not in table:
                raise _unknown_key(key, table, section, lineno)
            target[table[key]] = (value, lineno)

    if not saw_timeline:
        raise ScriptError("no `## Timeline` section found -- nothing to render")

    output = _apply_output(raw_output, base)
    silence = _apply_silence(raw_silence)
    defaults = _apply_defaults(raw_defaults)
    clips = _build_clips(timeline, assets, defaults, base)

    if not clips:
        raise ScriptError("the Timeline section is empty -- nothing to render")

    for warning in warnings:
        print(f"  warning: {warning}")

    return VideoScript(
        source=path,
        title=title,
        output=output,
        silence=silence,
        defaults=defaults,
        clips=clips,
    )


def _build_clips(
    items: list[tuple[str, int]],
    assets: dict[str, str],
    defaults: Defaults,
    base: Path,
) -> list[TimelineClip]:
    """Resolve raw timeline lines into clips, expanding folders and globs."""
    clips: list[TimelineClip] = []

    for content, lineno in items:
        source, option_text = _split_item(content)
        if not source:
            raise ScriptError(f"timeline item has no file or asset name: {content!r}", lineno)

        join, duration, trim = _parse_options(option_text, lineno)
        join = join if join is not None else defaults.join
        duration = duration if duration is not None else _default_duration(join, defaults)
        trim = trim if trim is not None else defaults.trim_silence

        # A zero-length blend is a cut; collapse it so the graph has no degenerate filters.
        if join is not Join.CUT and duration <= 0:
            join, duration = Join.CUT, 0.0

        alias = _norm_key(source)
        is_alias = alias in assets
        raw_path = assets[alias] if is_alias else source

        for resolved in _expand_source(_resolve_path(raw_path, base), lineno):
            clips.append(
                TimelineClip(
                    path=resolved,
                    label=source if is_alias else resolved.stem,
                    join=join,
                    join_duration=duration,
                    trim_silence=trim,
                    line=lineno,
                )
            )

    return clips


def _split_item(content: str) -> tuple[str, str]:
    """Split a timeline item into its source and its option text."""
    # A backticked source is honoured first so a path containing the separator parses.
    if content.startswith("`"):
        end = content.find("`", 1)
        if end != -1:
            return content[1:end].strip(), content[end + 1:].lstrip(" -\u2014\u2013|")

    parts = _OPTION_SPLIT_RE.split(content, maxsplit=1)
    return _clean_value(parts[0]), parts[1] if len(parts) > 1 else ""
