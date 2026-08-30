"""Every setting a script can carry, declared once.

The parser reads values through this table, the writer emits them through it,
and the reference docs are generated from it. Before, each setting was spelled
out in three places -- a key table, an apply function that knew its type, and a
line in the writer -- so adding one meant three edits and the writer could drift
from the parser without anything noticing. Here a setting is one line.

Sections group settings for the reader's benefit. Lookup is global: a setting is
understood wherever it appears, so a file written against an older layout still
parses. What the writer produces is the canonical arrangement.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Setting:
    """One `- key: value` line."""

    key: str                    # canonical spelling
    target: str                 # which VideoScript attribute holds it
    field: str                  # attribute on that object
    kind: str                   # how to read the value; see readers in the parser
    aliases: tuple[str, ...] = ()
    note: str = ""              # one line, for the generated reference
    unit: str = ""              # written after the value; the parser strips it
    # Written every time, or only when it differs from the dataclass default.
    always: bool = True


@dataclass(frozen=True)
class Section:
    """One `## Heading` and the settings conventionally written under it."""

    title: str
    settings: tuple[Setting, ...]
    aliases: tuple[str, ...] = ()
    blurb: str = ""


OUTPUT = Section(
    "Output",
    aliases=("output settings", "settings"),
    blurb="Where the file goes, and what kind of file it is.",
    settings=(
        Setting("file", "output", "file", "path", ("output", "path", "output file"),
                "The video to write; parent folders are created"),
        Setting("resolution", "output", "resolution", "resolution", ("size",),
                "`WxH`. Sources are letterboxed to fit"),
        Setting("fps", "output", "fps", "int", ("frame rate", "framerate"),
                "Output frame rate"),
        Setting("encoder", "output", "encoder", "text", ("video encoder", "codec"),
                "See encoders below"),
        Setting("quality", "output", "quality", "optional number", ("crf", "cq"),
                "Lower is bigger and better; blank takes the encoder's default",
                always=False),
        Setting("dry run", "output", "dry_run", "bool", (),
                "`yes` prints the resolved timeline and stops", always=False),
    ),
)

JOINS = Section(
    "Joins",
    aliases=("join", "defaults", "default", "global edits", "global", "globals", "master"),
    blurb="How clips meet each other, and the black at either end.",
    settings=(
        Setting("join", "defaults", "join", "join", ("transition",),
                "Join used when a timeline item does not name one"),
        Setting("crossfade", "defaults", "crossfade", "number", (),
                "Default `crossfade` length (seconds)"),
        Setting("fade", "defaults", "fade", "number", (),
                "Default `fade` length (seconds)"),
        Setting("audio overlap", "defaults", "audio_overlap", "number",
                ("prelap", "audio first"),
                "Default `audio overlap` length (seconds)"),
        Setting("audio blend", "defaults", "audio_blend", "optional number",
                ("audio crossfade",),
                "How long a join's sound takes to change hands; blank follows the picture",
                always=False),
        Setting("audio lead", "defaults", "audio_lead", "number", ("audio offset",),
                "Seconds the sound changes before the picture does"),
        Setting("fade from black", "defaults", "fade_in", "number", ("fade in",),
                "Opening fade, at the very start (seconds)"),
        Setting("fade to black", "defaults", "fade_out", "number", ("fade out",),
                "Closing fade, at the very end (seconds)"),
    ),
)

AUTO_EDITOR = Section(
    "Auto Editor",
    aliases=("auto edit", "passes", "silence", "trim silence", "dead space",
             "dead space removal"),
    blurb=(
        "Passes that edit the footage for you. Each is opt-in; with both off, "
        "clips render exactly as cut."
    ),
    settings=(
        Setting("balance audio", "balance", "enabled", "bool",
                ("balance", "balance levels"),
                "Level every clip to the same loudness"),
        Setting("audio target", "balance", "target_lufs", "number",
                ("target", "target loudness", "loudness"),
                "LUFS to level to; -14 is what YouTube normalises to", unit="LUFS"),
        Setting("trim silence", "defaults", "trim_silence", "bool", ("trim",),
                "Remove dead air, unless a timeline item says otherwise"),
        Setting("silence threshold", "silence", "threshold_db", "number",
                ("threshold", "threshold db"),
                "Silence floor in dB **below the clip's own peak**", unit="dB"),
        Setting("silence padding", "silence", "padding", "number", ("padding", "pad"),
                "Seconds kept around each loud region; gaps under 2x this merge"),
        Setting("silence min length", "silence", "min_silence", "number",
                ("min silence", "minimum silence"),
                "A silence must run this long to be cut"),
        Setting("silence min segment", "silence", "min_segment", "number",
                ("min segment", "minimum segment"),
                "Kept regions shorter than this are dropped"),
    ),
)

SECTIONS: tuple[Section, ...] = (OUTPUT, JOINS, AUTO_EDITOR)

# Sections that carry no settings, handled by the parser directly.
ASSETS_ALIASES = ("assets", "files", "sources")
TIMELINE_ALIASES = ("timeline", "sequence", "order")

# What a timeline item can say after its `--`. Declared here so the error a
# reader sees cannot drift from what the parser accepts -- it already did once,
# when a join was added and the message kept listing the old three.
ITEM_OPTIONS = (
    ("<join> [seconds]", "How this clip meets the one before it: "
                         "cut, crossfade, fade, audio overlap"),
    ("2:10-5:30", "Keep only this range; repeat for more"),
    ("trim silence", "Remove dead air from this clip"),
    ("keep silence", "Leave this clip's dead air alone"),
    ("volume [dB]", "This clip's level -- what auto-balance writes, "
                    "and what you adjust by hand"),
    ("audio blend [seconds]", "How long this join's sound takes to change hands"),
    ("audio lead [seconds]", "Seconds the sound changes before the picture"),
)


# Settings that existed once and no longer do, so a script carrying one is told
# what happened rather than "unknown setting".
RETIRED = {
    "audio adjust": "levelling replaces it -- see `## Auto Editor`",
    "audio gain": "levelling replaces it -- see `## Auto Editor`",
}


def _build_lookup() -> dict[str, Setting]:
    """Every spelling of every setting. Lookup is global, not per section."""
    table: dict[str, Setting] = {}
    for section in SECTIONS:
        for setting in section.settings:
            for spelling in (setting.key, *setting.aliases):
                if spelling in table:
                    raise AssertionError(
                        f"{spelling!r} means both {table[spelling].field} "
                        f"and {setting.field}"
                    )
                table[spelling] = setting
    return table


LOOKUP = _build_lookup()


def section_for(name: str) -> str | None:
    """Which section a heading names, or None if it is not one of ours."""
    for section in SECTIONS:
        if name == section.title.lower() or name in section.aliases:
            return "settings"
    if name in ASSETS_ALIASES:
        return "assets"
    if name in TIMELINE_ALIASES:
        return "timeline"
    return None
