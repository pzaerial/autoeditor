"""Regenerate the settings tables in .claude/script-format.md from the schema.

    python tools/gen_reference.py

The tables live between `<!-- generated:settings -->` markers. Everything else
in that file is written by hand -- this only owns the reference tables, which
are the part that goes stale when a setting is added.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoeditor import schema  # noqa: E402
from autoeditor.timeline import BalanceSettings, Defaults, Join, SilenceSettings  # noqa: E402

DOC = ROOT / ".claude" / "script-format.md"
START = "<!-- generated:settings -->"
END = "<!-- /generated:settings -->"

DEFAULTS = {
    "defaults": Defaults(),
    "silence": SilenceSettings(),
    "balance": BalanceSettings(),
    "output": None,
}
LITERAL = {
    "output": {"resolution": "1920x1080", "fps": "60", "encoder": "libx264",
               "file": "*required*", "quality": "per encoder", "dry run": "no"},
}


def default_for(setting) -> str:
    if setting.target in LITERAL and setting.key in LITERAL[setting.target]:
        return LITERAL[setting.target][setting.key]
    holder = DEFAULTS.get(setting.target)
    if holder is None:
        return ""
    value = getattr(holder, setting.field, None)
    if value is None:
        return "*follow picture*" if setting.kind == "optional number" else ""
    if isinstance(value, bool):
        return "`yes`" if value else "`no`"
    if isinstance(value, Join):
        return f"`{value.value}`"
    if isinstance(value, float):
        return f"`{value:g}`"
    return f"`{value}`"


def render() -> str:
    out = []
    for section in schema.SECTIONS:
        out.append(f"### `## {section.title}`")
        out.append("")
        if section.blurb:
            out.append(section.blurb)
            out.append("")
        others = ", ".join(f"`## {a.title()}`" for a in section.aliases)
        if others:
            out.append(f"Also accepted as {others}.")
            out.append("")
        out.append("| Key | Aliases | Default | Description |")
        out.append("|---|---|---|---|")
        for setting in section.settings:
            aliases = ", ".join(f"`{a}`" for a in setting.aliases)
            out.append(
                f"| `{setting.key}` | {aliases} | {default_for(setting)} | {setting.note} |"
            )
        out.append("")
    return "\n".join(out)


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print(f"markers not found in {DOC}", file=sys.stderr)
        return 1
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    DOC.write_text(f"{head}{START}\n\n{render()}\n{END}{tail}", encoding="utf-8")
    print(f"regenerated the settings tables in {DOC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
