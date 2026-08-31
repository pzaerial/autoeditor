"""Turning a v1 script's flat clip list into a v2 track timeline.

A v1 edit is one video track by definition -- that was the whole limitation --
so this is mostly a change of shape rather than of meaning: each clip's `join`
becomes the transition entry before it, its gain and silence flags become
effects, and its regions become in and out points.

The one place meaning has to be preserved carefully is `audio overlap`, which
v1 expressed as a join type that quietly dropped the head of the incoming
clip's picture. v2 keeps it as a named transition in the library and lets the
library say what it does, so nothing is lost and nothing is special-cased.

This runs on the *model*, not on the file, so it works the same for a script
parsed from markdown and for a project loaded from the app.
"""

from dataclasses import replace as _replace

from .timeline import Join, Region, TimelineClip, VideoScript
from .tracks import Clip, Effect, Project, Track, TrackKind


# What each v1 join is called now. `audio overlap` keeps its name: it is a real
# transition with its own behaviour, not a spelling of crossfade.
JOIN_NAMES = {
    Join.CUT: "cut",
    Join.CROSSFADE: "crossfade",
    Join.FADE: "dip to black",
    Join.AUDIO_OVERLAP: "audio overlap",
}

MAIN_TRACK = "Main"


def _effects_for(clip: TimelineClip) -> list[Effect]:
    """A v1 clip's flags and numbers, as entries from the library."""
    out: list[Effect] = []
    if clip.trim_silence:
        out.append(Effect("trim silence", {}))
    if clip.audio_gain_db:
        out.append(Effect("volume", {"db": clip.audio_gain_db}))
    return out


def _transition_for(clip: TimelineClip):
    """The transition joining this clip to the one before it."""
    from .tracks import Transition

    kind = JOIN_NAMES[clip.join]
    if clip.join is Join.CUT:
        return Transition("cut", 0.0, line=clip.line)

    # v1 stored the audio side as an override of the picture's: `audio_blend`
    # of None meant "match the picture", and a lead of 0 meant "no offset".
    # v2 says the same thing with None, so an untouched join carries no numbers
    # and the library's own defaults apply -- which is what makes an
    # `audio overlap` still land its sound on the picture cut.
    default_length = clip.join_duration
    audio_duration = None
    if clip.audio_blend is not None and abs(clip.audio_blend - default_length) > 1e-9:
        audio_duration = clip.audio_blend

    audio_lead = clip.audio_lead if abs(clip.audio_lead) > 1e-9 else None
    if clip.join is Join.AUDIO_OVERLAP and clip.audio_blend is not None:
        # An overlap's blend is the ramp at each edge, not the whole handover,
        # so it is carried even when it happens to equal the duration.
        audio_duration = clip.audio_blend

    return Transition(
        kind, clip.join_duration,
        audio_duration=audio_duration, audio_lead=audio_lead, line=clip.line,
    )


def _clips_from(clip: TimelineClip) -> list[tuple[Clip, object]]:
    """One v1 clip as (clip, transition-before-it) pairs.

    A clip narrowed to several ranges becomes several clips. Ranges that cut
    into each other could already be flattened this way in v1 -- that is what
    `expand_regions` did -- and doing it for every multi-range clip means the
    track holds exactly what plays, with no second notion of "regions" inside a
    clip that the timeline cannot show.
    """
    from .tracks import Transition

    base = Clip(
        source=clip.path,
        label=clip.label,
        effects=_effects_for(clip),
        missing=clip.missing,
        line=clip.line,
    )

    regions: list[Region] = list(clip.regions or [])
    if not regions:
        return [(base, _transition_for(clip))]

    out: list[tuple[Clip, object]] = []
    for i, region in enumerate(regions):
        piece = _replace(
            base,
            source_in=region.start,
            source_out=region.end,
            label=base.label if i == 0 else f"{base.label} ({i + 1})",
            effects=[Effect(e.name, dict(e.params)) for e in base.effects],
        )
        if i == 0:
            joins = _transition_for(clip)
        elif region.join is Join.CUT:
            joins = Transition("cut", 0.0, line=clip.line)
        else:
            joins = Transition(
                JOIN_NAMES[region.join], region.join_duration, line=clip.line
            )
        out.append((piece, joins))
    return out


def to_project(script: VideoScript) -> Project:
    """A v1 `VideoScript` as a v2 `Project`: one video track holding it all."""
    entries: list = []
    first = True
    for clip in script.clips:
        for piece, joins in _clips_from(clip):
            if not first and joins is not None:
                # A cut needs no entry of its own -- it is what a boundary means
                # when nothing else is said, and writing one on every line would
                # bury the transitions that matter.
                if not joins.is_cut():
                    entries.append(joins)
            entries.append(piece)
            first = False

    track = Track(TrackKind.VIDEO, MAIN_TRACK, entries)
    project = Project(
        source=script.source,
        title=script.title,
        output=script.output,
        silence=script.silence,
        defaults=script.defaults,
        balance=script.balance,
        tracks=[track],
    )
    project.validate()
    return project
