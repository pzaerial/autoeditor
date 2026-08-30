"""Parse a markdown video script into a VideoScript."""

import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import schema
from .timecode import format_timecode, parse_timecode
from .timeline import (
    VIDEO_EXTENSIONS,
    BalanceSettings,
    Defaults,
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


def _unknown_setting(key: str, line: int) -> ScriptError:
    known = sorted({setting.key for setting in schema.LOOKUP.values()})
    # A misspelling is far more likely than a genuinely unknown setting, and
    # the whole list is long enough to be worth skipping past.
    near = difflib.get_close_matches(key, sorted(schema.LOOKUP), n=1, cutoff=0.7)
    hint = f" Did you mean `{near[0]}`?" if near else ""
    return ScriptError(
        f"unknown setting {key!r}.{hint} Valid: {', '.join(known)}", line
    )


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
    "audio overlap": Join.AUDIO_OVERLAP,
    "audio first": Join.AUDIO_OVERLAP,
    "prelap": Join.AUDIO_OVERLAP,
    "pre lap": Join.AUDIO_OVERLAP,
    "jcut": Join.AUDIO_OVERLAP,
    "j cut": Join.AUDIO_OVERLAP,
}

_JOIN_OPTION_RE = re.compile(
    r"^(audio overlap|audio first|prelap|pre lap|jcut|j cut|hard cut|cut"
    r"|crossfade|dissolve|fade)"
    r"\s*(.*)$"
)
# `volume +3 dB`, `gain -2`, `audio 1.5` -- a straight level trim for one clip.
_GAIN_OPTION_RE = re.compile(r"^(volume|gain|audio)\s+([+-]?[\d.]+\s*(?:db)?)$")
# `balance +8.2 dB` -- how levelling used to record what it measured, when that
# was a second number alongside `volume`. Now there is one, and this adds into
# it, so a script written against the old pair still renders the same.
_BALANCE_OPTION_RE = re.compile(r"^balance\s+([+-]?[\d.]+\s*(?:db)?)$")
# `audio overlap 2`, `audio lead -1.5` -- where this join's sound sits.
_AUDIO_EDIT_RE = re.compile(
    r"^audio\s+(blend|crossfade|lead|offset)\s+"
    r"([+-]?[\d.]+\s*(?:s|sec|secs|seconds)?|auto|follow)$"
)
_AUDIO_EDIT_KEYS = {
    "blend": "audio_blend", "crossfade": "audio_blend",
    "lead": "audio_lead", "offset": "audio_lead",
}
_REGION_RE = re.compile(r"^([\d:.]+)\s*(?:-|to|–|—)\s*([\d:.]+)$")


@dataclass
class ItemOptions:
    join: Join | None = None
    duration: float | None = None
    trim: bool | None = None
    gain_db: float | None = None
    # A legacy `balance` option, kept apart from `volume` until both have been
    # read so the two spellings can appear in either order and still sum.
    legacy_balance_db: float = 0.0
    audio_blend: float | None = None
    audio_lead: float | None = None
    # `audio blend auto` asks for None, which a plain None cannot express.
    audio_blend_auto: bool = False
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
                opts.audio_blend_auto = True
            else:
                setattr(opts, field, _parse_number(value, edit_option.group(1), line))
            continue

        levelled = _BALANCE_OPTION_RE.match(raw_option.strip().lower())
        if levelled:
            opts.legacy_balance_db += _parse_number(
                levelled.group(1), "balance", line
            )
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
                f"unknown timeline option {raw_option.strip()!r}. Valid: "
                + ", ".join(name for name, _ in schema.ITEM_OPTIONS),
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
    if join is Join.AUDIO_OVERLAP:
        return defaults.audio_overlap
    return 0.0


def _read(setting, value: str, line: int):
    """Turn one written value into what the model wants to hold."""
    kind = setting.kind
    if kind == "text":
        return value
    if kind == "bool":
        return _parse_bool(value, setting.key, line)
    if kind == "int":
        return int(_parse_number(value, setting.key, line))
    if kind == "number":
        return _parse_number(value, setting.key, line)
    if kind == "optional number":
        if _norm_key(value) in ("", "auto", "default", "follow", "follow picture"):
            return None
        return _parse_number(value, setting.key, line)
    if kind == "resolution":
        if not _RESOLUTION_RE.match(value):
            raise ScriptError(f"{setting.key}: expected WxH, got {value!r}", line)
        return value.lower().replace("\u00d7", "x").replace(" ", "")
    if kind == "join":
        word = _norm_key(value)
        if word not in _JOIN_WORDS:
            valid = ", ".join(sorted({j.value for j in Join}))
            raise ScriptError(f"{setting.key}: expected {valid}, got {value!r}", line)
        return _JOIN_WORDS[word]
    raise AssertionError(f"unknown setting kind {kind!r}")


def _apply_settings(
    raw: list[tuple[object, str, int]], script_parts: dict, base: Path, strict: bool
) -> None:
    """Write every parsed setting onto the object that owns it."""
    for setting, value, line in raw:
        if setting.kind == "path":
            resolved = _resolve_path(value, base)
            # Loose parsing lets the UI load and fix a bad name; rendering re-checks.
            if strict:
                check_output_path(resolved, line)
            setattr(script_parts[setting.target], setting.field, resolved)
            continue
        setattr(script_parts[setting.target], setting.field, _read(setting, value, line))


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

    settings: list[tuple[object, str, int]] = []
    assets: dict[str, str] = {}
    timeline: list[tuple[str, int]] = []
    saw_timeline = False
    saw_file = False

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
            found = schema.section_for(name)
            # A top-level `# Heading` that is not a section name is the title.
            if len(heading.group(1)) == 1 and found is None:
                title = heading.group(2).strip()
                section = None
                continue
            section = found
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
            continue
        if section == "assets":
            key, value = _split_kv(content, lineno, "Assets")
            assets[key] = value
            continue

        key, value = _split_kv(content, lineno, "settings")
        if key in schema.RETIRED:
            warnings.append(f"line {lineno}: `{key}` was removed -- {schema.RETIRED[key]}")
            continue
        setting = schema.LOOKUP.get(key)
        if setting is None:
            raise _unknown_setting(key, lineno)
        # Last one wins, which is what a reader would expect of a repeated line.
        settings = [entry for entry in settings if entry[0] is not setting]
        settings.append((setting, value, lineno))
        saw_file = saw_file or setting.field == "file"

    if not saw_timeline:
        raise ScriptError("no `## Timeline` section found -- nothing to render")
    if not saw_file:
        raise ScriptError("the Output section must set `file:` (the video to write)")

    script = VideoScript(
        source=path,
        title=title,
        output=OutputSettings(file=Path("output.mp4")),
        silence=SilenceSettings(),
        defaults=Defaults(),
        balance=BalanceSettings(),
        clips=[],
    )
    _apply_settings(
        settings,
        {
            "output": script.output,
            "silence": script.silence,
            "defaults": script.defaults,
            "balance": script.balance,
        },
        base,
        strict,
    )

    script.clips = _build_clips(timeline, assets, script.defaults, base, strict)
    if not script.clips:
        raise ScriptError("the Timeline section is empty -- nothing to render")

    for warning in warnings:
        print(f"  warning: {warning}")

    return script


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
        # Rounded so folding a legacy `balance` into `volume` writes back the
        # number a person would read off, not 0.09999999999999964.
        gain = round((opts.gain_db or 0.0) + opts.legacy_balance_db, 4)

        # None means "sound follows picture"; an explicit `auto` asks for that
        # back when the project as a whole has been given an overlap.
        if opts.audio_blend_auto:
            blend = None
        elif opts.audio_blend is not None:
            blend = opts.audio_blend
        else:
            blend = defaults.audio_blend
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
                    audio_blend=blend,
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
