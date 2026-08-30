"""Parse a markdown video script into a VideoScript."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .timecode import format_timecode, parse_timecode
from .timeline import (
    VIDEO_EXTENSIONS,
    BalanceSettings,
    Defaults,
    GlobalEdits,
    Join,
    OutputSettings,
    Region,
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
    "joins": "defaults",
    "join": "defaults",
    "global edits": "globals",
    "global": "globals",
    "globals": "globals",
    "master": "globals",
    "auto editor": "autoedit",
    "auto edit": "autoedit",
    "passes": "autoedit",
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
    cleaned = re.sub(
        r"(?i)\s*(seconds|second|secs|sec|lufs|lu|dbtp|db|s)\s*$", "", value.strip()
    )
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


def check_output_path(path: Path, line: int | None = None) -> None:
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


def _expand_source(path: Path, line: int, strict: bool = True) -> list[Path]:
    """Expand a directory or glob into its video files, oldest first."""
    if any(ch in path.name for ch in "*?[") and not path.exists():
        matches = [p for p in path.parent.glob(path.name) if p.is_file()]
        if not matches:
            if strict:
                raise ScriptError(f"no files match {path}", line)
            return [path]
        return sorted(matches, key=lambda p: (p.stat().st_mtime, p.name))

    if path.is_dir():
        videos = [
            f for f in path.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        ]
        if not videos:
            if strict:
                raise ScriptError(f"no video files in folder {path}", line)
            return [path]
        return sorted(videos, key=lambda f: (f.stat().st_mtime, f.name))

    if not path.exists():
        if strict:
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
# `volume +3 dB`, `gain -2`, `audio 1.5` -- a straight level trim for one clip.
_GAIN_OPTION_RE = re.compile(r"^(volume|gain|audio)\s+([+-]?[\d.]+\s*(?:db)?)$")
# `balance +8.2 dB` -- what levelling measured, written back so a render need
# not measure it again.
_BALANCE_OPTION_RE = re.compile(r"^balance\s+([+-]?[\d.]+\s*(?:db)?)$")
# `audio overlap 2`, `audio lead -1.5` -- where this join's sound sits.
_AUDIO_EDIT_RE = re.compile(
    r"^audio\s+(overlap|blend|crossfade|lead|offset)\s+"
    r"([+-]?[\d.]+\s*(?:s|sec|secs|seconds)?|auto|follow)$"
)
_AUDIO_EDIT_KEYS = {
    "overlap": "audio_overlap", "blend": "audio_overlap", "crossfade": "audio_overlap",
    "lead": "audio_lead", "offset": "audio_lead",
}
_REGION_RE = re.compile(r"^([\d:.]+)\s*(?:-|to|–|—)\s*([\d:.]+)$")


@dataclass
class ItemOptions:
    join: Join | None = None
    duration: float | None = None
    trim: bool | None = None
    gain_db: float | None = None
    audio_overlap: float | None = None
    audio_lead: float | None = None
    balance_db: float | None = None
    # `audio overlap auto` asks for None, which a plain None cannot express.
    audio_overlap_auto: bool = False
    regions: list[Region] = field(default_factory=list)


def _parse_options(text: str, line: int) -> ItemOptions:
    """Parse the comma-separated options after a timeline item's separator.

    A join written before any range is the clip's join to the previous clip.
    A join written after one applies to the range that follows it, so regions
    of a single clip can blend into each other.
    """
    opts = ItemOptions()
    pending: tuple[Join, float | None] | None = None

    for raw_option in text.split(","):
        # Ranges are matched before normalising, which would eat their dash.
        region = _REGION_RE.match(raw_option.strip())
        if region:
            try:
                start = parse_timecode(region.group(1))
                end = parse_timecode(region.group(2))
            except ValueError as exc:
                raise ScriptError(f"{exc} in {raw_option.strip()!r}", line) from None
            if end <= start:
                raise ScriptError(
                    f"region {raw_option.strip()!r} ends before it starts", line
                )
            entry = Region(start, end)
            if pending and opts.regions:
                entry.join, duration = pending[0], pending[1]
                entry.join_duration = duration if duration is not None else -1.0
            pending = None
            opts.regions.append(entry)
            continue

        # Matched raw too: _norm_key turns a minus sign into a space.
        edit_option = _AUDIO_EDIT_RE.match(raw_option.strip().lower())
        if edit_option:
            field, value = _AUDIO_EDIT_KEYS[edit_option.group(1)], edit_option.group(2)
            if value in ("auto", "follow"):
                if field == "audio_lead":
                    raise ScriptError("audio lead needs a number of seconds", line)
                opts.audio_overlap_auto = True
            else:
                setattr(opts, field, _parse_number(value, edit_option.group(1), line))
            continue

        levelled = _BALANCE_OPTION_RE.match(raw_option.strip().lower())
        if levelled:
            opts.balance_db = _parse_number(levelled.group(1), "balance", line)
            continue

        gain = _GAIN_OPTION_RE.match(raw_option.strip().lower())
        if gain:
            opts.gain_db = _parse_number(gain.group(2), gain.group(1), line)
            continue

        option = _norm_key(raw_option)
        if not option:
            continue

        match = _JOIN_OPTION_RE.match(option)
        if match:
            name, rest = match.group(1), match.group(2).strip()
            duration = _parse_number(rest, name, line) if rest else None
            if opts.regions:
                pending = (_JOIN_WORDS[name], duration)
            else:
                opts.join = _JOIN_WORDS[name]
                opts.duration = duration
            continue

        if option in ("trim silence", "trim", "remove silence", "remove dead space"):
            opts.trim = True
        elif option in ("keep silence", "no trim", "no trim silence"):
            opts.trim = False
        else:
            raise ScriptError(
                f"unknown timeline option {raw_option.strip()!r}. Valid: cut, "
                "crossfade [seconds], fade [seconds], trim silence, keep silence, "
                "volume [dB], balance [dB], audio overlap [seconds], "
                "audio lead [seconds], "
                "or a range like 2:10-5:30",
                line,
            )

    return opts


def _resolve_regions(
    regions: list[Region], defaults: Defaults, line: int
) -> list[Region] | None:
    """Sort regions, fill in default join durations, and reject overlaps."""
    if not regions:
        return None

    ordered = sorted(regions, key=lambda r: r.start)
    for i, region in enumerate(ordered):
        if region.join_duration < 0:
            region.join_duration = _default_duration(region.join, defaults)
        if region.join is not Join.CUT and region.join_duration <= 0:
            region.join, region.join_duration = Join.CUT, 0.0
        if i and region.start < ordered[i - 1].end - 1e-6:
            raise ScriptError(
                f"regions {format_timecode(ordered[i - 1].start)}-"
                f"{format_timecode(ordered[i - 1].end)} and "
                f"{format_timecode(region.start)}-{format_timecode(region.end)} overlap",
                line,
            )
    ordered[0].join, ordered[0].join_duration = Join.CUT, 0.0
    return ordered


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
    "quality": "quality", "crf": "quality", "cq": "quality",
    # Global edits, historically written here; they now live in their own
    # section and are routed there, so old scripts keep working.
    "fade in": "fade_in",
    "fade out": "fade_out",
    "audio adjust": "audio_gain_db",
    "dry run": "dry_run",
}

_GLOBAL_FROM_OUTPUT = {"fade_in", "fade_out", "audio_gain_db"}

_GLOBAL_KEYS = {
    "fade in": "fade_in",
    "fade out": "fade_out",
    "audio adjust": "audio_gain_db", "audio gain": "audio_gain_db",
    "volume": "audio_gain_db", "gain": "audio_gain_db",
}

_AUTOEDIT_KEYS = {
    "balance audio": "enabled", "balance": "enabled", "balance levels": "enabled",
    "audio target": "target_lufs", "target": "target_lufs",
    "target loudness": "target_lufs", "loudness": "target_lufs",
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
    "audio overlap": "audio_overlap", "audio blend": "audio_overlap",
    "audio crossfade": "audio_overlap",
    "audio lead": "audio_lead", "audio offset": "audio_lead",
}


def _apply_output(raw: dict[str, tuple[str, int]], base: Path, strict: bool = True) -> OutputSettings:
    if "file" not in raw:
        raise ScriptError("the Output section must set `file:` (the .mp4 to write)")

    out = OutputSettings(file=_resolve_path(raw["file"][0], base))
    # Loose parsing lets the UI load and fix a bad name; rendering re-checks.
    if strict:
        check_output_path(out.file, raw["file"][1])

    for field_name, (value, line) in raw.items():
        if field_name in _GLOBAL_FROM_OUTPUT or field_name == "file":
            continue
        if field_name == "resolution":
            if not _RESOLUTION_RE.match(value):
                raise ScriptError(f"resolution: expected WxH, got {value!r}", line)
            out.resolution = value.lower().replace("\u00d7", "x").replace(" ", "")
        elif field_name == "fps":
            out.fps = int(_parse_number(value, "fps", line))
        elif field_name == "encoder":
            out.encoder = value
        elif field_name == "quality":
            out.quality = _parse_number(value, "quality", line)
        elif field_name == "dry_run":
            out.dry_run = _parse_bool(value, "dry run", line)
        else:
            setattr(out, field_name, _parse_number(value, field_name, line))

    return out


def _apply_globals(raw: dict[str, tuple[str, int]]) -> GlobalEdits:
    edits = GlobalEdits()
    for field_name, (value, line) in raw.items():
        setattr(edits, field_name, _parse_number(value, field_name, line))
    return edits


def _apply_autoedit(raw: dict[str, tuple[str, int]]) -> BalanceSettings:
    settings = BalanceSettings()
    for field_name, (value, line) in raw.items():
        if field_name == "enabled":
            settings.enabled = _parse_bool(value, "balance audio", line)
        else:
            settings.target_lufs = _parse_number(value, field_name, line)
    return settings


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


def parse_script(path: Path, *, strict: bool = True) -> VideoScript:
    """Parse a markdown video script; strict=False keeps missing files as placeholders."""
    if not path.exists():
        raise ScriptError(f"script file not found: {path}")

    base = path.resolve().parent
    text = path.read_text(encoding="utf-8-sig")

    title = path.stem
    section: str | None = None
    in_fence = False
    warnings: list[str] = []

    raw_output: dict[str, tuple[str, int]] = {}
    raw_globals: dict[str, tuple[str, int]] = {}
    raw_autoedit: dict[str, tuple[str, int]] = {}
    raw_silence: dict[str, tuple[str, int]] = {}
    raw_defaults: dict[str, tuple[str, int]] = {}
    assets: dict[str, str] = {}
    timeline: list[tuple[str, int]] = []
    saw_timeline = False

    section_tables = {
        "output": (_OUTPUT_KEYS, raw_output),
        "globals": (_GLOBAL_KEYS, raw_globals),
        "autoedit": (_AUTOEDIT_KEYS, raw_autoedit),
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

    output = _apply_output(raw_output, base, strict)
    # An explicit Global Edits section wins over the same key left in Output.
    inherited = {
        k: v for k, v in raw_output.items()
        if k in _GLOBAL_FROM_OUTPUT and k not in raw_globals
    }
    edits = _apply_globals({**inherited, **raw_globals})
    silence = _apply_silence(raw_silence)
    balance = _apply_autoedit(raw_autoedit)
    defaults = _apply_defaults(raw_defaults)
    clips = _build_clips(timeline, assets, defaults, base, strict)

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
        globals=edits,
        balance=balance,
        clips=clips,
    )


def _build_clips(
    items: list[tuple[str, int]],
    assets: dict[str, str],
    defaults: Defaults,
    base: Path,
    strict: bool = True,
) -> list[TimelineClip]:
    """Resolve raw timeline lines into clips, expanding folders and globs."""
    clips: list[TimelineClip] = []

    for content, lineno in items:
        source, option_text = _split_item(content)
        if not source:
            raise ScriptError(f"timeline item has no file or asset name: {content!r}", lineno)

        opts = _parse_options(option_text, lineno)
        join = opts.join if opts.join is not None else defaults.join
        duration = opts.duration if opts.duration is not None else _default_duration(join, defaults)
        trim = opts.trim if opts.trim is not None else defaults.trim_silence
        gain = opts.gain_db if opts.gain_db is not None else 0.0

        # None means "sound follows picture"; an explicit `auto` asks for that
        # back when the project as a whole has been given an overlap.
        if opts.audio_overlap_auto:
            overlap = None
        elif opts.audio_overlap is not None:
            overlap = opts.audio_overlap
        else:
            overlap = defaults.audio_overlap
        lead = opts.audio_lead if opts.audio_lead is not None else defaults.audio_lead

        # A zero-length blend is a cut; collapse it so the graph has no degenerate filters.
        if join is not Join.CUT and duration <= 0:
            join, duration = Join.CUT, 0.0

        regions = _resolve_regions(opts.regions, defaults, lineno)

        alias = _norm_key(source)
        is_alias = alias in assets
        raw_path = assets[alias] if is_alias else source

        for resolved in _expand_source(_resolve_path(raw_path, base), lineno, strict):
            clips.append(
                TimelineClip(
                    path=resolved,
                    label=source if is_alias else resolved.stem,
                    join=join,
                    join_duration=duration,
                    trim_silence=trim,
                    audio_gain_db=gain,
                    balance_db=opts.balance_db,
                    audio_overlap=overlap,
                    audio_lead=lead,
                    line=lineno,
                    regions=regions,
                    missing=not resolved.is_file(),
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
