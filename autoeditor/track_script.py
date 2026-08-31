"""Reading and writing a track timeline as markdown.

A track is a section; its entries are a numbered list of clips and the
transitions between them; what is done to an entry hangs off it as bullets:

    ## Video: Main
    1. `intro.mp4`
       - volume -9.6 dB
    2. crossfade 0.3
    3. `stream.mp4` @ 1:05:29.724-1:10:09.021
       - trim silence
    4. audio overlap 69
       - audio 12
    5. `outro.mp4`

    ## Audio: Music
    - gain: -18 dB
    1. `bed.mp3` at 0:05
       - fade in 2

Transitions are entries in their own right rather than options on the clip that
follows, because that is what they are on a timeline: something between two
clips, that you can point at and give settings of its own. A bullet at the left
margin is a setting for the whole track; an indented one belongs to the entry
above it.

Settings sections (`## Output`, `## Auto Editor`, `## Defaults`) are unchanged
and are read by `script_parser`, which also still reads the old single
`## Timeline`. A file written against that opens here unchanged -- `parse_project`
migrates it -- and saving rewrites it in this shape.
"""

import re
from pathlib import Path

from . import effects, schema
from .script_parser import (
    ScriptError, _FENCE_RE, _HEADING_RE, _apply_settings, _clean_value,
    _expand_source, _norm_key, _parse_bool, _parse_number, _resolve_path,
    _split_kv, _unknown_setting, parse_script,
)
from .timecode import parse_timecode
from .timeline import BalanceSettings, Defaults, OutputSettings, SilenceSettings
from .tracks import Clip, Effect, Project, Track, TrackKind, Transition


# `## Video: Main`, `## Audio: Music`. The kind is part of the heading because a
# track's kind decides what can go on it, and guessing it from the first file
# would make a track change meaning when its first clip does.
_TRACK_HEADING_RE = re.compile(r"^(video|audio)\s*[:–—-]\s*(.+)$", re.I)

# Indentation is meaningful here, so this cannot use the shared item regex.
_ENTRY_RE = re.compile(r"^(\s*)(?:([-*+])|(\d+)[.)])\s+(.*)$")

# `` `path` `` or a bare asset name, then optional `@ in-out` and `at start`.
_CLIP_RE = re.compile(
    r"^(?:`(?P<quoted>[^`]+)`|(?P<bare>[^@]+?))"
    r"(?:\s*@\s*(?P<in>[\d:.]+)\s*(?:-|to|–|—)\s*(?P<out>[\d:.]+))?"
    r"(?:\s+at\s+(?P<at>[\d:.]+))?\s*$"
)
_NUMBER_RE = re.compile(r"^([+-]?[\d.]+)\s*(?:db|s|sec|secs|seconds)?$", re.I)

_TRACK_SETTINGS = {"gain", "volume", "level", "muted", "mute", "hidden", "hide"}


def _spellings(defs):
    """Every name an effect or transition answers to, longest first.

    Longest first because `fade in` and `fade` are both real, and a shorter
    name must never win against the longer one it is a prefix of.
    """
    out = []
    for item in defs:
        for name in (item.name, *item.aliases):
            out.append((name, item))
    return sorted(out, key=lambda pair: -len(pair[0]))


_EFFECT_SPELLINGS = _spellings(effects.EFFECTS)
_TRANSITION_SPELLINGS = _spellings(effects.TRANSITIONS)


def _match_spelling(text: str, table):
    """Split `text` into (definition, remainder) on the longest name that fits."""
    low = text.strip().lower()
    for name, item in table:
        if low == name:
            return item, ""
        if low.startswith(name) and low[len(name)] in " \t":
            return item, text.strip()[len(name):].strip()
    return None, text


def _value_for(param, raw: str, line: int):
    """One parameter's value, read the way its kind says to."""
    if param.kind == "bool":
        return _parse_bool(raw, param.name, line)
    if param.kind == "choice":
        if raw.lower() not in param.choices:
            raise ScriptError(
                f"{param.name}: expected one of {', '.join(param.choices)}, "
                f"got {raw!r}", line)
        return raw.lower()
    found = _NUMBER_RE.match(raw.strip())
    if not found:
        raise ScriptError(f"{param.name}: expected a number, got {raw!r}", line)
    return float(found.group(1))


# ---------------------------------------------------------------- parsing

def _parse_effect(text: str, line: int) -> Effect:
    """One bullet under a clip, as an entry from the library."""
    found, rest = _match_spelling(text, _EFFECT_SPELLINGS)
    if found is None:
        known = ", ".join(e.name for e in effects.EFFECTS)
        raise ScriptError(
            f"unknown effect {text!r} -- the library has: {known}", line)
    params: dict = {}
    if rest and found.params:
        params[found.params[0].name] = _value_for(found.params[0], rest, line)
    elif rest:
        raise ScriptError(f"`{found.name}` takes no value, got {rest!r}", line)
    return Effect(found.name, params)


def _parse_transition(text: str, line: int) -> Transition | None:
    """An entry that names a transition, or None if it does not."""
    found, rest = _match_spelling(text, _TRANSITION_SPELLINGS)
    if found is None:
        return None
    duration = 0.0
    if rest:
        number = _NUMBER_RE.match(rest)
        if not number:
            raise ScriptError(
                f"{found.name}: expected a duration, got {rest!r}", line)
        duration = float(number.group(1))
    elif found.params:
        duration = float(found.params[0].default)
    return Transition(found.name, duration, line=line)


def _apply_transition_bullet(joins: Transition, text: str, line: int) -> None:
    """`audio 12` / `lead 4` under a transition: its sound's own timing."""
    key, _, rest = text.strip().partition(" ")
    key = key.lower().rstrip(":")
    rest = rest.strip().rstrip(":")
    if key in ("audio", "blend", "audio blend"):
        joins.audio_duration = _parse_number(rest, "audio", line)
    elif key in ("lead", "offset", "audio lead"):
        joins.audio_lead = _parse_number(rest, "lead", line)
    else:
        raise ScriptError(
            f"a transition takes `audio <seconds>` or `lead <seconds>`, "
            f"got {text!r}", line)


def _apply_track_setting(track: Track, text: str, line: int) -> None:
    key, value = _split_kv(text, line, f"track {track.name!r}")
    if key in ("gain", "volume", "level"):
        track.gain_db = _parse_number(value, "gain", line)
    elif key in ("muted", "mute"):
        track.muted = _parse_bool(value, "muted", line)
    elif key in ("hidden", "hide"):
        track.hidden = _parse_bool(value, "hidden", line)
    else:
        raise ScriptError(
            f"a track takes `gain`, `muted` or `hidden`, got {key!r}", line)


def _parse_clip(text: str, assets: dict, base: Path, line: int,
                strict: bool) -> list[Clip]:
    """One entry naming a source, as the clips it stands for."""
    found = _CLIP_RE.match(text)
    if not found:
        raise ScriptError(f"cannot read timeline entry {text!r}", line)
    raw = (found.group("quoted") or found.group("bare") or "").strip()
    if not raw:
        raise ScriptError(f"timeline entry has no file or asset name: {text!r}", line)

    raw = assets.get(_norm_key(raw), raw)
    path = _resolve_path(_clean_value(raw), base)
    sources = _expand_source(path, line, strict)

    source_in = parse_timecode(found.group("in")) if found.group("in") else 0.0
    source_out = parse_timecode(found.group("out")) if found.group("out") else None
    start = parse_timecode(found.group("at")) if found.group("at") else None

    out = []
    for i, one in enumerate(sources):
        out.append(Clip(
            source=one,
            label=one.stem,
            source_in=source_in if i == 0 else 0.0,
            source_out=source_out if i == 0 else None,
            # A folder or glob standing for many files places only the first;
            # the rest follow it, which is what "these, in order" means.
            start=start if i == 0 else None,
            missing=not one.is_file(),
            line=line,
        ))
    return out


def parse_project(path: Path, *, strict: bool = True) -> Project:
    """Read a script as a track timeline, migrating an older one on the way."""
    if not path.exists():
        raise ScriptError(f"script file not found: {path}")

    base = path.resolve().parent
    text = path.read_text(encoding="utf-8-sig")

    title = path.stem
    section: str | None = None
    track: Track | None = None
    tracks: list[Track] = []
    settings: list[tuple[object, str, int]] = []
    assets: dict[str, str] = {}
    warnings: list[str] = []
    current: object = None          # the entry a nested bullet belongs to
    in_fence = False
    saw_v1_timeline = False
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
            body = heading.group(2).strip()
            as_track = _TRACK_HEADING_RE.match(body)
            if as_track:
                kind = (TrackKind.VIDEO if as_track.group(1).lower() == "video"
                        else TrackKind.AUDIO)
                track = Track(kind, as_track.group(2).strip(), [])
                tracks.append(track)
                section, current = "track", None
                continue

            name = _norm_key(body)
            found = schema.section_for(name)
            if len(heading.group(1)) == 1 and found is None:
                title = body
                section, track, current = None, None, None
                continue
            section, track, current = found, None, None
            if found == "timeline":
                saw_v1_timeline = True
            elif found is None:
                warnings.append(f"line {lineno}: ignoring unknown section '{name}'")
            continue

        entry = _ENTRY_RE.match(line)
        if not entry or section is None:
            continue
        indent, bullet, number, content = entry.groups()
        content = content.strip()
        if not content:
            continue

        if section == "track" and track is not None:
            if number is not None:
                joins = _parse_transition(content, lineno)
                if joins is not None:
                    track.entries.append(joins)
                    current = joins
                else:
                    clips = _parse_clip(content, assets, base, lineno, strict)
                    track.entries += clips
                    current = clips[-1] if clips else None
            elif not indent:
                _apply_track_setting(track, content, lineno)
            elif isinstance(current, Transition):
                _apply_transition_bullet(current, content, lineno)
            elif isinstance(current, Clip):
                current.effects.append(_parse_effect(content, lineno))
            else:
                raise ScriptError(
                    f"nothing for this to belong to: {content!r}", lineno)
            continue

        if section == "timeline":
            continue                      # handled by the v1 reader below
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
        settings = [e for e in settings if e[0] is not setting]
        settings.append((setting, value, lineno))
        saw_file = saw_file or setting.field == "file"

    # An older script has no tracks at all -- read it the old way and convert,
    # so a file written before any of this still opens and renders the same.
    if not tracks and saw_v1_timeline:
        from .migrate import to_project
        return to_project(parse_script(path, strict=strict))

    if not tracks:
        raise ScriptError(
            "no track sections found -- add one, such as `## Video: Main`")
    if not saw_file:
        raise ScriptError("the Output section must set `file:` (the video to write)")

    project = Project(
        source=path,
        title=title,
        output=OutputSettings(file=Path("output.mp4")),
        silence=SilenceSettings(),
        defaults=Defaults(),
        balance=BalanceSettings(),
        tracks=tracks,
    )
    _apply_settings(
        settings,
        {"output": project.output, "silence": project.silence,
         "defaults": project.defaults, "balance": project.balance},
        base, strict,
    )
    project.validate()
    if not project.all_clips():
        raise ScriptError("every track is empty -- nothing to render")

    # `trim silence` in `## Auto Editor` seeds every clip that has not said
    # otherwise. The timeline lists what is done to each clip, so the default
    # is resolved once here rather than consulted again at render time -- and
    # `keep silence` is how one clip opts out, after which the marker has done
    # its job and is dropped.
    for _, clip in project.all_clips():
        opted_out = clip.has("keep silence")
        if project.defaults.trim_silence and not opted_out and not clip.trim_silence:
            clip.effects.append(Effect("trim silence", {}))
        if opted_out:
            clip.remove_effect("keep silence")

    for warning in warnings:
        print(f"  warning: {warning}")
    return project


# ---------------------------------------------------------------- writing

def _fmt(value: float) -> str:
    return f"{value:g}"


def _effect_line(item: Effect) -> str:
    found = effects.effect_def(item.name)
    if found is None or not found.params:
        return item.name
    first = found.params[0]
    value = item.params.get(first.name)
    if value is None:
        return item.name
    if first.kind == "bool":
        return f"{item.name} {'yes' if value else 'no'}"
    unit = f" {first.unit}" if first.unit else ""
    sign = "+" if first.unit == "dB" and value > 0 else ""
    return f"{item.name} {sign}{_fmt(float(value))}{unit}"


def _clip_line(clip: Clip) -> str:
    text = f"`{clip.source}`"
    if clip.source_in > 0 or clip.source_out is not None:
        from .timecode import format_timecode
        end = clip.source_out if clip.source_out is not None else 0.0
        text += f" @ {format_timecode(clip.source_in)}-{format_timecode(end)}"
    if clip.start is not None:
        from .timecode import format_timecode
        text += f" at {format_timecode(clip.start)}"
    return text


def to_markdown(project: Project, *, notes: str = "") -> str:
    """Serialise a project to markdown that parses back to the same edit."""
    from .script_writer import _settings_lines

    lines: list[str] = [f"# {project.title}", ""]
    if notes:
        lines += [notes.strip(), ""]
    lines += _settings_lines(project)

    for track in project.tracks:
        lines.append(f"## {track.kind.value.title()}: {track.name}")
        lines.append("")
        if track.gain_db:
            lines.append(f"- gain: {track.gain_db:+g} dB")
        if track.muted:
            lines.append("- muted: yes")
        if track.hidden:
            lines.append("- hidden: yes")
        if track.gain_db or track.muted or track.hidden:
            lines.append("")

        # With trimming on for the whole edit, a clip that is not trimmed is
        # the notable one, so it is what gets written.
        default_trim = project.defaults.trim_silence
        number = 0
        for item in track.entries:
            number += 1
            if isinstance(item, Transition):
                # A cut is what a boundary means when nothing is said, so it is
                # only written when someone asked for it explicitly.
                head = item.kind
                if item.duration:
                    head += f" {_fmt(item.duration)}"
                lines.append(f"{number}. {head}")
                if item.audio_duration is not None:
                    lines.append(f"   - audio {_fmt(item.audio_duration)}")
                if item.audio_lead:
                    lines.append(f"   - lead {_fmt(item.audio_lead)}")
            else:
                lines.append(f"{number}. {_clip_line(item)}")
                for effect in item.effects:
                    if default_trim and effect.name == "trim silence":
                        continue
                    lines.append(f"   - {_effect_line(effect)}")
                if default_trim and not item.has("trim silence"):
                    lines.append("   - keep silence")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
