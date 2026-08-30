"""Render a VideoScript back to the markdown script format.

Section settings are emitted straight from `schema.SECTIONS`, so the writer
cannot name a setting the parser does not know, or forget one that was added.
Only the timeline items are formatted by hand, because their options are
positional and conditional in ways a table does not capture.
"""

import re

from . import schema
from .timecode import format_timecode
from .timeline import Defaults, Join, VideoScript

# An option like `crossfade 0.5` or `cut`, for spotting the join on item one.
_JOIN_OPTION = re.compile(r"^([a-z ]+?)(?:\s+[\d.+-]+)?$")
_JOIN_WORDS = {join.value for join in Join}


def _fmt(value: float) -> str:
    return f"{value:g}"


def _write_value(setting, value) -> str:
    """One setting's value, spelled the way the parser reads it back."""
    if setting.kind == "path":
        return f"`{value}`"
    if setting.kind == "bool":
        return "yes" if value else "no"
    if setting.kind == "join":
        return value.value
    if setting.kind == "int":
        return str(int(value))
    if setting.kind in ("number", "optional number"):
        return _fmt(value)
    return str(value)


def _settings_lines(script: VideoScript) -> list[str]:
    """Every section and setting, in the order the schema declares them."""
    from .timeline import BalanceSettings, OutputSettings, SilenceSettings

    holders = {
        "output": script.output,
        "silence": script.silence,
        "defaults": script.defaults,
        "balance": script.balance,
    }
    blanks = {
        "output": OutputSettings(file=script.output.file),
        "silence": SilenceSettings(),
        "defaults": Defaults(),
        "balance": BalanceSettings(),
    }

    lines: list[str] = []
    for section in schema.SECTIONS:
        rows = []
        for setting in section.settings:
            value = getattr(holders[setting.target], setting.field)
            if value is None:
                continue
            if not setting.always and value == getattr(blanks[setting.target], setting.field):
                continue
            unit = f" {setting.unit}" if setting.unit else ""
            rows.append(f"- {setting.key}: {_write_value(setting, value)}{unit}")
        if rows:
            lines += [f"## {section.title}", "", *rows, ""]
    return lines


def _item_options(clip, defaults: Defaults) -> list[str]:
    """The options needed to reproduce this clip, omitting anything default."""
    options: list[str] = []

    if clip.join is not defaults.join:
        if clip.join is Join.CUT:
            options.append("cut")
        else:
            options.append(f"{clip.join.value} {_fmt(clip.join_duration)}")
    elif clip.join is not Join.CUT:
        default_duration = {
            Join.CROSSFADE: defaults.crossfade,
            Join.FADE: defaults.fade,
            Join.AUDIO_OVERLAP: defaults.audio_overlap,
        }[clip.join]
        if abs(clip.join_duration - default_duration) > 1e-9:
            options.append(f"{clip.join.value} {_fmt(clip.join_duration)}")

    for i, region in enumerate(clip.regions or []):
        # A join before a range binds that range to the one before it.
        if i and region.join is not Join.CUT:
            options.append(f"{region.join.value} {_fmt(region.join_duration)}")
        options.append(f"{format_timecode(region.start)}-{format_timecode(region.end)}")

    if clip.trim_silence != defaults.trim_silence:
        options.append("trim silence" if clip.trim_silence else "keep silence")

    if clip.audio_gain_db:
        options.append(f"volume {clip.audio_gain_db:+g} dB")

    if clip.audio_blend != defaults.audio_blend:
        options.append(
            "audio blend auto" if clip.audio_blend is None
            else f"audio blend {_fmt(clip.audio_blend)}"
        )
    if abs(clip.audio_lead - defaults.audio_lead) > 1e-9:
        options.append(f"audio lead {_fmt(clip.audio_lead)}")

    return options


def to_markdown(script: VideoScript, *, notes: str = "") -> str:
    """Serialise a script to markdown that parses back to the same edit."""
    lines: list[str] = [f"# {script.title}", ""]
    if notes:
        lines += [notes.strip(), ""]

    lines += _settings_lines(script)
    lines += ["## Timeline", ""]

    for i, clip in enumerate(script.clips, 1):
        options = _item_options(clip, script.defaults)
        # The first clip has nothing before it, so its join is noise.
        if i == 1 and options:
            head = _JOIN_OPTION.match(options[0])
            if head and head.group(1).strip() in _JOIN_WORDS:
                options = options[1:]
        suffix = f" -- {', '.join(options)}" if options else ""
        lines.append(f"{i}. `{clip.path}`{suffix}")

    return "\n".join(lines) + "\n"
