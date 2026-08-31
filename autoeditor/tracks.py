"""The timeline: tracks of clips, with transitions between them.

The model this replaces was a flat list of clips, each carrying a join that
described how it attached to the one before it. Position was implicit and
audio was derived from the picture's offsets, which is why there could only
ever be one video track and why sound could only be moved off its picture
through two escape hatches bolted onto the following clip.

Here a track is an ordered list of entries -- clips and the transitions between
them -- and a clip's position is a property of the track, not of the clip. That
one change is what makes layers, overlays, music beds and independent audio
timing expressible at all, and it is why `Join` is gone: an `audio overlap` is
now a crossfade whose audio side carries a lead, which is what it always was.
"""

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from . import effects


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".wmv", ".flv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


class TrackKind(Enum):
    VIDEO = "video"
    AUDIO = "audio"


class TimelineError(Exception):
    """A timeline that cannot mean anything -- two transitions in a row, say."""


@dataclass
class Effect:
    """One entry from the library, with the settings this clip gives it."""

    name: str
    params: dict = field(default_factory=dict)

    def get(self, key: str, fallback=0.0):
        value = self.params.get(key, fallback)
        return fallback if value is None else value


@dataclass
class Clip:
    """A piece of a source file, placed on a track."""

    source: Path
    label: str = ""
    # What part of the file to read. `source_out` of None means "to the end",
    # which is only resolvable once the file has been probed.
    source_in: float = 0.0
    source_out: float | None = None
    # Where it lands on the timeline. None means "follow the clip before me",
    # which is what keeps an ordinary cut list free of absolute times.
    start: float | None = None
    effects: list[Effect] = field(default_factory=list)
    # Shared by the picture and sound halves of one source, so the app can move
    # them together until someone asks it not to.
    link: str | None = None
    missing: bool = False
    line: int = 0

    # -- effects ---------------------------------------------------------

    def effect(self, name: str) -> Effect | None:
        canonical = effects.canonical_effect(name)
        for item in self.effects:
            if item.name == canonical:
                return item
        return None

    def has(self, name: str) -> bool:
        return self.effect(name) is not None

    def set_effect(self, name: str, **params) -> None:
        canonical = effects.canonical_effect(name)
        if canonical is None:
            raise KeyError(f"no such effect: {name}")
        existing = self.effect(canonical)
        if existing is None:
            self.effects.append(Effect(canonical, dict(params)))
        else:
            existing.params.update(params)

    def remove_effect(self, name: str) -> None:
        canonical = effects.canonical_effect(name)
        self.effects = [e for e in self.effects if e.name != canonical]

    @property
    def gain_db(self) -> float:
        """This clip's level, the one number auto-balance and a person share."""
        found = self.effect("volume")
        return float(found.get("db", 0.0)) if found else 0.0

    @property
    def trim_silence(self) -> bool:
        return self.has("trim silence")

    @property
    def source_length(self) -> float | None:
        """Length of the chosen range, when the out point is known."""
        if self.source_out is None:
            return None
        return max(0.0, self.source_out - self.source_in)


@dataclass
class Transition:
    """How two neighbouring clips on a track meet."""

    kind: str = "cut"
    duration: float = 0.0
    # The sound's own length and timing across this join. Either left as None
    # takes what the library says this kind of transition does -- a crossfade's
    # sound is centred on the picture cut, an audio overlap's ends on it -- so
    # the common case needs no numbers and an unusual one is still expressible.
    # `audio_lead` is seconds the sound changes hands *before* the picture:
    # positive is a J-cut, negative an L-cut.
    audio_duration: float | None = None
    audio_lead: float | None = None
    line: int = 0

    @property
    def overlap(self) -> float:
        """Seconds the two clips overlap on the timeline."""
        return effects.overlap_of(self.kind, self.duration)

    @property
    def head_trim(self) -> float:
        """Seconds of picture the incoming clip gives up to this transition."""
        return effects.head_trim_of(self.kind, self.duration)

    @property
    def audio_span(self) -> float:
        """How much of the sound's change-over the picture already pays for.

        A crossfade's pictures overlap for its whole length, and a dip to black
        leaves each side its own length of material at the boundary to fade
        through -- either way the transition's duration is free. An `audio
        overlap` is the exception: its picture gave that time up at the head,
        so nothing is free at the boundary and the sound has to reach back for
        all of it.
        """
        return 0.0 if self.head_trim > 0 else self.duration

    def audio_length(self) -> float:
        """How long the sound takes to change hands."""
        if self.audio_duration is not None:
            return max(0.0, self.audio_duration)
        return effects.transition_def(self.kind).audio_default_length(self.duration)

    def lead(self) -> float:
        """Seconds the sound changes hands before the picture does."""
        return self.audio_lead or 0.0

    @property
    def audio_mode(self) -> str:
        """How the sound changes hands: "crossfade" or "under"."""
        return effects.mode_of(self.kind)

    def audio_follows_picture(self) -> bool:
        """True when the sound needs no timeline of its own."""
        if self.audio_mode != "crossfade":
            return False
        return (
            abs(self.lead()) < 1e-9
            and abs(self.audio_length() - self.duration) < 1e-9
        )

    def is_cut(self) -> bool:
        return self.kind == "cut" or self.duration <= 0


@dataclass
class Placement:
    """One clip, resolved to where it actually sits on the timeline."""

    index: int              # position among the track's clips
    clip: Clip
    start: float
    length: float
    before: Transition | None
    after: Transition | None

    @property
    def end(self) -> float:
        return self.start + self.length


@dataclass
class Track:
    kind: TrackKind = TrackKind.VIDEO
    name: str = ""
    # Clips and transitions in one list, because that is what the script and
    # the timeline both show. `validate` holds it to alternating order.
    entries: list = field(default_factory=list)
    gain_db: float = 0.0
    muted: bool = False
    hidden: bool = False

    # -- shape -----------------------------------------------------------

    def clips(self) -> list[Clip]:
        return [e for e in self.entries if isinstance(e, Clip)]

    def transitions(self) -> list[Transition]:
        return [e for e in self.entries if isinstance(e, Transition)]

    def transition_before(self, index: int) -> Transition | None:
        """The transition joining clip `index` to the one before it."""
        seen = -1
        previous = None
        for entry in self.entries:
            if isinstance(entry, Transition):
                previous = entry
                continue
            seen += 1
            if seen == index:
                return previous if seen > 0 else None
            previous = None
        return None

    def validate(self) -> None:
        """Reject a track that cannot mean anything."""
        if not self.entries:
            return
        if isinstance(self.entries[0], Transition):
            raise TimelineError(
                f"track {self.name!r} opens with a transition, "
                "which has nothing before it to come from"
            )
        if isinstance(self.entries[-1], Transition):
            raise TimelineError(
                f"track {self.name!r} ends with a transition, "
                "which has nothing after it to go to"
            )
        for a, b in zip(self.entries, self.entries[1:]):
            if isinstance(a, Transition) and isinstance(b, Transition):
                raise TimelineError(
                    f"track {self.name!r} has two transitions in a row "
                    f"({a.kind} then {b.kind}); a clip has to sit between them"
                )

    # -- layout ----------------------------------------------------------

    def laid_out(self, lengths: list[float]) -> list[Placement]:
        """Where each clip sits, given how long each one runs.

        Clips are sequential: each starts where the last one ended, minus the
        overlap of the transition joining them. A clip with an explicit `start`
        anchors there instead and the chain resumes from it -- which is what
        lets an overlay or a music bed sit anywhere while an ordinary cut list
        still needs no absolute times at all.
        """
        clips = self.clips()
        if len(lengths) != len(clips):
            raise TimelineError(
                f"track {self.name!r}: {len(clips)} clip(s) but "
                f"{len(lengths)} length(s)"
            )

        out: list[Placement] = []
        cursor = 0.0
        for i, clip in enumerate(clips):
            before = self.transition_before(i)
            after = self.transition_before(i + 1) if i + 1 < len(clips) else None
            if clip.start is not None:
                start = clip.start
            elif i == 0:
                start = 0.0
            else:
                start = cursor - (before.overlap if before else 0.0)
            start = max(0.0, start)
            out.append(Placement(i, clip, start, lengths[i], before, after))
            cursor = start + lengths[i]
        return out

    def duration(self, lengths: list[float]) -> float:
        placements = self.laid_out(lengths)
        return max((p.end for p in placements), default=0.0)


@dataclass
class Project:
    """A whole edit: where it goes, how it is processed, and what is in it."""

    source: Path
    title: str
    output: object                      # OutputSettings
    silence: object                     # SilenceSettings
    defaults: object                    # Defaults
    balance: object                     # BalanceSettings
    # Stacking order is list order: the first video track is the bottom layer,
    # so a later one covers it. Audio tracks all mix together, so their order
    # is presentation only.
    tracks: list[Track] = field(default_factory=list)

    def video_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind is TrackKind.VIDEO]

    def audio_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind is TrackKind.AUDIO]

    def all_clips(self) -> list[tuple[Track, Clip]]:
        """Every clip in the project, paired with the track holding it."""
        return [(track, clip) for track in self.tracks for clip in track.clips()]

    def validate(self) -> None:
        for track in self.tracks:
            track.validate()

    def track_named(self, name: str) -> Track | None:
        for track in self.tracks:
            if track.name.lower() == name.lower():
                return track
        return None

    def describe(self, lengths: dict[int, list[float]] | None = None) -> str:
        """Rundown of the edit, printed before rendering.

        `lengths` maps a track's position to its clips' timeline lengths. Given
        them, every clip is shown at the time it actually plays; without them
        the shape of the edit is still readable, which is what a parse-only
        check wants.
        """
        out: list[str] = []
        for position, track in enumerate(self.tracks):
            clips = track.clips()
            flags = []
            if track.muted:
                flags.append("muted")
            if track.hidden:
                flags.append("hidden")
            if track.gain_db:
                flags.append(f"{track.gain_db:+g} dB")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            out.append(
                f"  {track.kind.value}: {track.name}  "
                f"({len(clips)} clip(s)){suffix}"
            )

            placements = None
            if lengths and position in lengths:
                try:
                    placements = track.laid_out(lengths[position])
                except TimelineError:
                    placements = None

            width = max((len(c.label or c.source.name) for c in clips), default=0)
            for i, clip in enumerate(clips):
                before = track.transition_before(i)
                join = ""
                if before is not None and not before.is_cut():
                    join = f"{before.kind} {before.duration:g}s"
                    if not before.audio_follows_picture():
                        join += (f" (audio {before.audio_length():g}s "
                                 f"@ {before.lead():+g}s)")
                elif before is not None:
                    join = "cut"

                marks = []
                for item in clip.effects:
                    if item.name == "volume":
                        marks.append(f"{item.get('db', 0.0):+g} dB")
                    elif item.params:
                        first = next(iter(item.params.values()))
                        marks.append(f"{item.name} {first:g}")
                    else:
                        marks.append(item.name)
                if clip.source_out is not None or clip.source_in:
                    marks.append(
                        f"in {clip.source_in:g}"
                        + (f" out {clip.source_out:g}" if clip.source_out else "")
                    )
                if clip.missing:
                    marks.append("MISSING")

                when = ""
                if placements is not None:
                    p = placements[i]
                    when = f"{p.start:8.2f}s "
                name = clip.label or clip.source.name
                out.append(
                    f"    {when}{i + 1:>3}. {name:<{width}}  {join:<18}"
                    + (f"  [{']  ['.join(marks)}]" if marks else "")
                )
                out.append(f"         {clip.source}")
        return "\n".join(out)
