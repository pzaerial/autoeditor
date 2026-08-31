"""The settings sections of a script, emitted from the schema.

Every section and setting comes straight from `schema.SECTIONS`, so the writer
cannot name a setting the parser does not know, or forget one that was added.
The timeline itself is written by `track_script.to_markdown`, which is the only
caller of `_settings_lines` -- the two halves of a script, from the two places
that know their shapes.
"""

from . import schema
from .timeline import Defaults


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


def _settings_lines(script) -> list[str]:
    """Every section and setting, in the order the schema declares them.

    Takes anything carrying the four settings objects, which is both a Project
    and the older VideoScript -- they hold the same settings and differ only in
    what comes after them.
    """
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
