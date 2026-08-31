"""The library of effects and transitions, declared once.

An effect is something applied *to* a clip; a transition is something applied
*between* two of them. Both are declared here as data -- name, aliases, the
parameters they take, and a line of help -- with the filter fragments they emit
kept alongside in `_EMIT` / the transition helpers.

That split is deliberate. The definitions are plain data, so the parser, the
writer, the reference docs and the app's inspector can all read the same
registry and none of them can fall behind it; the emitters are code, because
writing a filter chain is not something a table can express. It is the shape
`schema.py` already uses for settings, applied to the part of the model that is
meant to grow.

Adding an effect is one `EffectDef` and one entry in `_EMIT`.
"""

from dataclasses import dataclass, field


# Equal power, the curve an editor expects of an audio crossfade. Two unrelated
# signals ramped linearly sum to about -3 dB where they meet, so a long linear
# crossfade audibly sags in the middle; a quarter-sine pair holds the level flat
# because sin^2 + cos^2 = 1. Inaudible across a 0.3 s join, obvious across ten.
CROSSFADE_CURVE = "qsin"

# -1 dBFS as linear amplitude, which is what alimiter wants.
OUTPUT_CEILING = 10 ** (-1.0 / 20)


# ---------------------------------------------------------------- declarations

@dataclass(frozen=True)
class Param:
    """One setting an effect takes, described well enough to build a control."""

    name: str
    kind: str = "number"          # number | bool | choice
    default: float | bool | str = 0.0
    unit: str = ""                # shown after the input: dB, s
    minimum: float | None = None
    maximum: float | None = None
    step: float = 0.1
    choices: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class EffectDef:
    """Something applied to one clip."""

    name: str
    applies: str                  # audio | video | both
    params: tuple[Param, ...] = ()
    aliases: tuple[str, ...] = ()
    # "filter" emits into the clip's chain; "source" changes what is *read* of
    # the clip and is handled before the graph exists (trim silence).
    kind: str = "filter"
    note: str = ""

    def touches(self, stream: str) -> bool:
        return self.applies in ("both", stream)


@dataclass(frozen=True)
class TransitionDef:
    """Something applied between two clips on the same track."""

    name: str
    params: tuple[Param, ...] = ()
    aliases: tuple[str, ...] = ()
    # Seconds the two clips overlap on the timeline. A crossfade overlaps by its
    # whole length; a dip to black does not overlap at all -- it plays one clip
    # out and the next in, which is why it lengthens the edit and a crossfade
    # shortens it. Layout asks only this, so a new transition slots in by
    # answering it.
    overlaps: bool = False
    # The incoming clip gives up this many seconds of *picture* from its head.
    # Only `audio overlap` does: its sound is heard under the outgoing picture
    # for exactly the stretch of its own picture that it gave up, which is what
    # keeps that clip's sound and picture locked to each other.
    trims_incoming: bool = False
    # How the sound changes hands. Two ways, and they are genuinely different
    # operations rather than one with different numbers:
    #
    #   "crossfade"  the two sounds swap over `blend` seconds, centred `lead`
    #                before the picture. Handles are borrowed symmetrically, so
    #                what one clip gains the other gives up and nothing shifts.
    #   "under"      the incoming sound starts `duration` early and simply plays
    #                *under* the outgoing one, each ramping over `blend` at its
    #                own edge. Both are at full level in between -- which is the
    #                point of a prelap: the new music arrives beneath the old
    #                picture, rather than trading places with it for a moment.
    #
    # Trying to express "under" as a centred crossfade quietly shortened the
    # outgoing clip, because a short blend put the whole swap before the cut.
    audio_mode: str = "crossfade"
    # The sound's default length, as a multiple of the transition's duration.
    audio_length_scale: float = 1.0
    # The curve each side's level follows. Equal power (`qsin`) is right when
    # two signals overlap and sum -- a linear pair sags about 3 dB where they
    # meet, inaudible over 0.3 s and obvious over ten. A dip to black has
    # nothing to sum against: each side falls to real silence, so it takes
    # ffmpeg's default straight line, which is also what an editor expects of
    # a fade. "" means "say nothing and take the default".
    audio_curve: str = CROSSFADE_CURVE
    note: str = ""

    def audio_default_length(self, duration: float) -> float:
        """How long this transition's sound takes when the clip does not say."""
        return duration * self.audio_length_scale


# ---------------------------------------------------------------- the effects

SECONDS = dict(kind="number", unit="s", minimum=0.0, step=0.1)

EFFECTS: tuple[EffectDef, ...] = (
    EffectDef(
        "volume", "audio",
        (Param("db", unit="dB", minimum=-60.0, maximum=24.0, step=0.5,
               note="Raised (+) or lowered (-) from the source's own level"),),
        aliases=("gain", "level"),
        note="This clip's level. Auto-balance writes it; edit it and your value stands",
    ),
    EffectDef(
        "fade in", "both",
        (Param("seconds", default=0.5, **SECONDS),),
        aliases=("fadein",),
        note="Up from black and silence at the clip's own start",
    ),
    EffectDef(
        "fade out", "both",
        (Param("seconds", default=0.5, **SECONDS),),
        aliases=("fadeout",),
        note="Down to black and silence at the clip's own end",
    ),
    EffectDef(
        "trim silence", "audio", (),
        aliases=("trim", "remove silence", "remove dead space"),
        kind="source",
        note="Drop this clip's dead air before anything else looks at it",
    ),
    EffectDef(
        "keep silence", "audio", (),
        aliases=("no trim", "no trim silence"),
        kind="marker",
        note="Leave this clip's dead air alone, even with trimming on for the "
             "whole edit",
    ),
)


TRANSITIONS: tuple[TransitionDef, ...] = (
    TransitionDef(
        "cut", (),
        aliases=("hard cut",),
        note="Straight from one clip to the next",
    ),
    TransitionDef(
        "crossfade",
        (Param("duration", default=0.3, **SECONDS),
         Param("audio", kind="number", unit="s", minimum=0.0, step=0.1,
               note="Audio's own length; blank follows the picture"),
         Param("lead", kind="number", unit="s", step=0.1,
               note="Seconds the sound changes before the picture: "
                    "+ is a J-cut, - an L-cut")),
        aliases=("dissolve", "mix"),
        overlaps=True,
        note="The two clips overlap and blend for the whole duration",
    ),
    TransitionDef(
        "dip to black",
        (Param("duration", default=0.5, **SECONDS),),
        aliases=("fade", "dip"),
        audio_curve="",
        note="The first clip falls to black, the second rises out of it",
    ),
    TransitionDef(
        "audio overlap",
        (Param("duration", default=2.0, **SECONDS),
         Param("audio", kind="number", unit="s", minimum=0.0, step=0.1,
               note="Audio's own length; blank uses the whole overlap"),
         Param("lead", kind="number", unit="s", step=0.1,
               note="Blank centres the sound so it lands on the picture cut")),
        aliases=("audio first", "prelap", "j-cut", "jcut"),
        trims_incoming=True,
        audio_mode="under",
        note="This clip is heard before it is seen; its picture gives up "
             "exactly the seconds its sound runs early",
    ),
)


# ---------------------------------------------------------------- lookup

def _index(defs) -> dict[str, object]:
    table: dict[str, object] = {}
    for item in defs:
        for spelling in (item.name, *item.aliases):
            if spelling in table:
                raise AssertionError(f"{spelling!r} is declared twice")
            table[spelling] = item
    return table


_EFFECT_BY_NAME = _index(EFFECTS)
_TRANSITION_BY_NAME = _index(TRANSITIONS)


def effect_def(name: str) -> EffectDef | None:
    return _EFFECT_BY_NAME.get(name.strip().lower())


def transition_def(name: str) -> TransitionDef | None:
    return _TRANSITION_BY_NAME.get(name.strip().lower())


def canonical_effect(name: str) -> str | None:
    found = effect_def(name)
    return found.name if found else None


def canonical_transition(name: str) -> str | None:
    found = transition_def(name)
    return found.name if found else None


def effect_names() -> list[str]:
    return [e.name for e in EFFECTS]


def transition_names() -> list[str]:
    return [t.name for t in TRANSITIONS]


# ---------------------------------------------------------------- emitting

def _emit_volume(params, stream, duration):
    db = float(params.get("db", 0.0))
    return [] if abs(db) < 1e-9 else [f"volume={db:g}dB"]


def _emit_fade_in(params, stream, duration):
    seconds = float(params.get("seconds", 0.0))
    if seconds <= 0:
        return []
    if stream == "video":
        return [f"fade=t=in:st=0:d={seconds:g}"]
    return [f"afade=t=in:st=0:d={seconds:g}"]


def _emit_fade_out(params, stream, duration):
    seconds = float(params.get("seconds", 0.0))
    if seconds <= 0:
        return []
    start = max(0.0, duration - seconds)
    if stream == "video":
        return [f"fade=t=out:st={start:.3f}:d={seconds:g}"]
    return [f"afade=t=out:st={start:.3f}:d={seconds:g}"]


_EMIT = {
    "volume": _emit_volume,
    "fade in": _emit_fade_in,
    "fade out": _emit_fade_out,
    "trim silence": None,        # a source effect; probe.py handles it
}


def emit(name: str, params: dict, stream: str, duration: float) -> list[str]:
    """Filter fragments for one effect on one stream, in chain order.

    `duration` is how long the clip runs on the timeline, which a fade-out needs
    to know where it starts. An effect that does not touch this stream, or that
    is not a filter at all, contributes nothing.
    """
    found = effect_def(name)
    if found is None:
        raise KeyError(f"no such effect: {name}")
    if found.kind != "filter" or not found.touches(stream):
        return []
    emitter = _EMIT.get(found.name)
    return emitter(params or {}, stream, duration) if emitter else []


def overlap_of(name: str, duration: float) -> float:
    """Seconds two clips overlap when joined by this transition."""
    found = transition_def(name)
    if found is None:
        raise KeyError(f"no such transition: {name}")
    return max(0.0, duration) if found.overlaps else 0.0


def mode_of(name: str) -> str:
    """How this transition's sound changes hands: "crossfade" or "under"."""
    found = transition_def(name)
    return found.audio_mode if found else "crossfade"


def curve_of(name: str) -> str:
    """The fade curve this transition's sound follows; "" is ffmpeg's default."""
    found = transition_def(name)
    return found.audio_curve if found else CROSSFADE_CURVE


def head_trim_of(name: str, duration: float) -> float:
    """Seconds of picture the incoming clip gives up to this transition."""
    found = transition_def(name)
    if found is None:
        raise KeyError(f"no such transition: {name}")
    return max(0.0, duration) if found.trims_incoming else 0.0


def describe_library() -> dict:
    """The registry as plain data, for the app's inspector and the docs."""
    def params(items):
        return [
            {"name": p.name, "kind": p.kind, "default": p.default, "unit": p.unit,
             "min": p.minimum, "max": p.maximum, "step": p.step,
             "choices": list(p.choices), "note": p.note}
            for p in items
        ]
    return {
        "effects": [
            {"name": e.name, "applies": e.applies, "kind": e.kind,
             "aliases": list(e.aliases), "note": e.note, "params": params(e.params)}
            for e in EFFECTS
        ],
        "transitions": [
            # `overlaps` and `trims_incoming` are how a transition sits on the
            # timeline, and the app lays its clips out with them -- so they have
            # to travel with the declaration rather than being reimplemented in
            # the front end, where they would quietly drift out of agreement.
            {"name": t.name, "aliases": list(t.aliases), "overlaps": t.overlaps,
             "trims_incoming": t.trims_incoming, "audio_mode": t.audio_mode,
             "note": t.note, "params": params(t.params)}
            for t in TRANSITIONS
        ],
    }
