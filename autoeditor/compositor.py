"""Compositing a track timeline into one filter_complex.

Every clip on every track is an input to a single ffmpeg call, and this wires
them together: each one trimmed to what the edit keeps, normalised, run through
its effects, faded at the edges its transitions ask for, and then *placed* --
at a time, on a layer -- rather than concatenated into a chain.

Placement is what makes this general. The old graph built a chain, so the
timeline could only ever be one strip of video with sound derived from it; here
a track is a canvas that clips are laid onto, tracks stack, and audio is mixed
from wherever it happens to sit. Nothing runs ffmpeg in this module, which
makes it the piece to read when an edit comes out wrong.
"""

from . import effects
from .effects import CROSSFADE_CURVE, OUTPUT_CEILING
from .probe import ClipInfo
from .tracks import Project, Track, TrackKind

# The longest an audio transition may borrow from one side of a clip, as a
# fraction of it. Past this a join would be eating the clip's middle rather
# than its handles, which is never what was meant.
MAX_HANDLE_FRACTION = 0.45

# Everything is resampled to this before it is placed, so a position on the
# timeline can be stated as a whole number of samples.
SAMPLE_RATE = 48000


# ---------------------------------------------------------------- geometry

def clip_lengths(project: Project, infos: list[ClipInfo]) -> list[list[float]]:
    """Each clip's timeline length, grouped by track, in `all_clips()` order."""
    out: list[list[float]] = []
    position = 0
    for track in project.tracks:
        count = len(track.clips())
        out.append([infos[position + i].effective_duration for i in range(count)])
        position += count
    return out


def input_bases(project: Project) -> list[int]:
    """The input number of each track's first clip."""
    bases, position = [], 0
    for track in project.tracks:
        bases.append(position)
        position += len(track.clips())
    return bases


def project_duration(project: Project, infos: list[ClipInfo]) -> float:
    """How long the finished video runs.

    The picture decides: an audio track running past the last frame is a music
    bed that outlasts the edit, and it gets cut off rather than extending it.
    With no video at all there is nothing else to ask, so the sound decides.
    """
    lengths = clip_lengths(project, infos)
    video = [
        track.duration(lengths[i])
        for i, track in enumerate(project.tracks)
        if track.kind is TrackKind.VIDEO and not track.hidden
    ]
    if video:
        return max(video)
    audio = [
        track.duration(lengths[i])
        for i, track in enumerate(project.tracks)
        if not track.muted
    ]
    return max(audio, default=0.0)


# ---------------------------------------------------------------- audio timing

def audio_handles(track: Track, placements, infos: list[ClipInfo], base: int):
    """Where each clip's sound sits, and how much source it borrows to get there.

    A transition's sound is a crossfade `blend` seconds long whose centre sits
    `lead` seconds before the centre of the picture transition. Both sides are
    paid for out of the clips' own source beyond their in and out points, so the
    timeline never shifts and only the join asked about loses lock with the
    picture:

        head = lead + (blend - picture_overlap) / 2   before this clip's in point
        tail = (blend - picture_overlap) / 2 - lead   after the last one's out point

    With no lead and a blend matching the picture both are zero and every clip
    lands exactly where its picture does. The formula is the whole story for
    every transition in the library -- an `audio overlap` reaches back its full
    length because the library gives it a lead of half its duration, not because
    anything here knows its name.
    """
    count = len(placements)
    head = [0.0] * count
    tail = [0.0] * count
    fade_in = [0.0] * count
    fade_out = [0.0] * count
    # Which curve each edge follows is the transition's business, not the
    # clip's, so it is carried alongside the length rather than assumed here.
    curve_in = [""] * count
    curve_out = [""] * count
    short: list[tuple[float, float] | None] = [None] * count

    ranges = [
        infos[base + i].keep_intervals or [(0.0, infos[base + i].duration)]
        for i in range(count)
    ]

    for i in range(1, count):
        joins = placements[i].before
        if joins is None:
            continue
        blend = joins.audio_length()
        lead = joins.lead()
        span = joins.audio_span
        if joins.audio_mode == "under":
            # The incoming sound plays *under* the outgoing one for the whole
            # overlap -- its picture gave up exactly that much -- so it reaches
            # back the full duration and the outgoing clip gives up nothing.
            want_head = joins.duration + lead
            want_tail = 0.0
        else:
            # The two swap over `blend`, centred `lead` before the picture. What
            # one side takes the other gives, so the timeline never shifts.
            half = (blend - span) / 2
            want_head = lead + half
            want_tail = half - lead

        # Only what the source actually has either side, and never so much that
        # a clip is asked to give up its own middle. A negative figure is not a
        # shortfall -- it is an L-cut holding its sound back -- so the lower
        # clamp is a fraction of the clip, not zero.
        room_head = ranges[i][0][0]
        room_tail = infos[base + i - 1].duration - ranges[i - 1][-1][1]
        head[i] = min(
            max(want_head, -MAX_HANDLE_FRACTION * infos[base + i].effective_duration),
            room_head,
        )
        tail[i - 1] = min(
            max(want_tail,
                -MAX_HANDLE_FRACTION * infos[base + i - 1].effective_duration),
            room_tail,
        )

        # Both sides ramp across the whole overlap unless asked for less, so an
        # `audio overlap` arrives over its full length rather than snapping in.
        got = max(0.0, span + head[i] + tail[i - 1])
        fade_in[i] = min(blend, got)
        fade_out[i - 1] = min(blend, got)
        curve_in[i] = curve_out[i - 1] = effects.curve_of(joins.kind)

        # An overlap is paid for out of source either side of the cut. A clip
        # used to its last frame has none to give and the ask quietly shrinks,
        # so record it rather than letting the edit look broken.
        asked = span + want_head + want_tail
        if asked - got > 0.01:
            short[i] = (asked, got)

    layout = []
    for i, placement in enumerate(placements):
        info = infos[base + i]
        spans = [[a, b] for a, b in ranges[i]]
        spans[0][0] -= head[i]
        spans[-1][1] += tail[i]
        kept = [
            (max(0.0, a), min(info.duration, b))
            for a, b in spans
            if min(info.duration, b) - max(0.0, a) > 1e-6
        ]
        layout.append({
            "start": max(0.0, placement.start - head[i]),
            "duration": sum(b - a for a, b in kept),
            "intervals": kept,
            "fade_in": fade_in[i],
            "fade_out": fade_out[i],
            "curve_in": curve_in[i],
            "curve_out": curve_out[i],
            "short": short[i],
        })
    return layout


def audio_notes(project: Project, infos: list[ClipInfo]) -> list[str]:
    """Joins whose sound the sources could not pay for in full."""
    lengths = clip_lengths(project, infos)
    bases = input_bases(project)
    notes: list[str] = []
    for position, track in enumerate(project.tracks):
        placements = track.laid_out(lengths[position])
        layout = audio_handles(track, placements, infos, bases[position])
        for i, item in enumerate(layout):
            if not item["short"]:
                continue
            asked, granted = item["short"]
            name = placements[i].clip.label or placements[i].clip.source.name
            notes.append(
                f"{track.name}: {name} asked for {asked:g}s of audio blend, "
                f"the sources could pay for {max(0.0, granted):.2f}s"
            )
    return notes


# ---------------------------------------------------------------- fragments

def _pin(duration: float) -> str:
    """Force an audio stream to exactly `duration`, padding or clipping it.

    A decoder's idea of a clip's length is not the timeline's: AAC pads its
    final frame by up to ~20 ms, and a capture can hand back an audio track
    shorter than its video. Placement believes whatever it is given, so without
    this each clip's slack pushes its sound out of step with its own picture.
    """
    return f"apad,atrim=end={duration:.6f},asetpts=PTS-STARTPTS"


def _trim_concat(parts, label, index, intervals, kind, full_length=None):
    """Cut a stream down to the intervals kept, as one stream again.

    A clip that keeps all of itself is handed back untouched. That is not only
    one filter fewer: trimming before resampling leaves the resampler a
    different edge to work from than trimming after it, so a clip that needed
    no trimming at all must not acquire one.
    """
    prefix = "a" if kind == "a" else ""
    count = len(intervals)
    if (
        full_length is not None
        and count == 1
        and intervals[0][0] <= 1e-9
        and intervals[0][1] >= full_length - 1e-9
    ):
        return label
    if count == 1:
        a, b = intervals[0]
        out = f"[{kind}t{index}]"
        parts.append(
            f"{label}{prefix}trim=start={a:.3f}:end={b:.3f},"
            f"{prefix}setpts=PTS-STARTPTS{out}"
        )
        return out

    splits = "".join(f"[{kind}s{index}_{j}]" for j in range(count))
    parts.append(f"{label}{prefix}split={count}{splits}")
    pieces = []
    for j, (a, b) in enumerate(intervals):
        parts.append(
            f"[{kind}s{index}_{j}]{prefix}trim=start={a:.3f}:end={b:.3f},"
            f"{prefix}setpts=PTS-STARTPTS[{kind}p{index}_{j}]"
        )
        pieces.append(f"[{kind}p{index}_{j}]")
    out = f"[{kind}t{index}]"
    parts.append(
        f"{''.join(pieces)}concat=n={count}:v={0 if kind == 'a' else 1}:"
        f"a={1 if kind == 'a' else 0}{out}"
    )
    return out


def _clip_effects(clip, stream: str, duration: float) -> list[str]:
    """The clip's own effects, in the order it lists them."""
    out: list[str] = []
    for item in clip.effects:
        out += effects.emit(item.name, item.params, stream, duration)
    return out


# ---------------------------------------------------------------- video

def is_sequential(placements) -> bool:
    """True when a track is one unbroken strip: clip after clip, no gaps.

    Which it is for any ordinary cut list, and is not the moment something is
    placed at a time of its own -- an overlay, a title, a clip pulled aside to
    leave a hole. The two cases want genuinely different graphs, so the answer
    is worth asking once.
    """
    if not placements:
        return False
    cursor = 0.0
    for p in placements:
        if p.clip.start is not None:
            return False
        if abs(p.start - max(0.0, cursor - (p.before.overlap if p.before else 0.0))) > 1e-6:
            return False
        cursor = p.start + p.length
    return abs(placements[0].start) < 1e-6


def _normalised(parts, project, placements, base, infos, alpha: bool):
    """Every clip on a track, trimmed and brought to the output's size and rate."""
    width, height = project.output.size
    fps = project.output.fps
    pixels = "yuva420p" if alpha else "yuv420p"
    bars = "black@0.0" if alpha else "black"
    labels = []

    for i, placement in enumerate(placements):
        index = base + i
        info = infos[index]
        if not info.has_video:
            labels.append(None)
            continue

        source = f"[{index}:v]"
        if info.keep_intervals:
            source = _trim_concat(parts, source, index, info.keep_intervals, "v",
                                  full_length=info.duration)
        chain = [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"format={pixels}",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{bars}",
            f"fps={fps}",
            "setsar=1",
        ]
        chain += _clip_effects(placement.clip, "video", placement.length)
        parts.append(f"{source}{','.join(chain)}[vn{index}]")
        labels.append(f"[vn{index}]")
    return labels


def _runs(placements) -> list[list[int]]:
    """Maximal stretches joined by transitions whose pictures overlap."""
    runs: list[list[int]] = []
    for i, placement in enumerate(placements):
        overlaps = placement.before is not None and placement.before.overlap > 0
        if not runs or (i and not overlaps):
            runs.append([])
        runs[-1].append(i)
    return runs


def _run_length(placements, run) -> float:
    return sum(placements[i].length for i in run) - sum(
        placements[i].before.overlap for i in run[1:]
    )


def rides_the_picture(placements) -> bool:
    """True when no transition on this track asks its sound to leave its picture."""
    return all(
        p.before is None or p.before.audio_follows_picture() for p in placements
    )


def _build_audio_strip(parts, project, placements, base, infos, tag):
    """A sequential track whose sound rides its picture, built as a chain.

    Placing and mixing gives the same answer, and it is what any other shape of
    track needs -- but it holds every clip's decoder open across the whole
    edit. On a long HD timeline that was enough to starve the mix: with the
    picture pulling one file far ahead, `amix` reached the end of the clips it
    could see and finished early, and everything past the first clip came out
    silent. A chain pulls each source only as the timeline reaches it.
    """
    labels: list[str] = []
    for i, placement in enumerate(placements):
        index = base + i
        info = infos[index]
        length = placement.length
        if not info.has_audio:
            parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}:"
                f"duration={length:.6f},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[an{index}]"
            )
            labels.append(f"[an{index}]")
            continue

        source = f"[{index}:a]"
        if info.keep_intervals:
            source = _trim_concat(parts, source, index, info.keep_intervals, "a",
                                  full_length=info.duration)
        chain = [f"aresample={SAMPLE_RATE}"]
        chain += _clip_effects(placement.clip, "audio", length)
        chain.append(_pin(length))
        chain.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
        parts.append(f"{source}{','.join(chain)}[an{index}]")
        labels.append(f"[an{index}]")

    strips: list[str] = []
    for r, run in enumerate(_runs(placements)):
        label = labels[run[0]]
        for k in range(len(run) - 1):
            nxt = run[k + 1]
            blend = placements[nxt].before.overlap
            out = f"[ax{tag}_{r}_{k}]"
            parts.append(
                f"{label}{labels[nxt]}acrossfade=d={blend}"
                f":c1={CROSSFADE_CURVE}:c2={CROSSFADE_CURVE}{out}"
            )
            label = out

        chain = []
        first, last = placements[run[0]], placements[run[-1]]
        if first.before is not None and first.before.kind == "dip to black" \
                and first.before.duration > 0:
            chain.append(f"afade=t=in:st=0:d={first.before.duration:g}")
        if last.after is not None and last.after.kind == "dip to black" \
                and last.after.duration > 0:
            start = max(0.0, _run_length(placements, run) - last.after.duration)
            chain.append(f"afade=t=out:st={start:.3f}:d={last.after.duration:g}")
        if chain:
            out = f"[agf{tag}_{r}]"
            parts.append(f"{label}{','.join(chain)}{out}")
            label = out
        strips.append(label)

    if len(strips) == 1:
        return strips[0]
    out = f"[aseq{tag}]"
    parts.append(f"{''.join(strips)}concat=n={len(strips)}:v=0:a=1{out}")
    return out


def _build_video_strip(parts, project, placements, base, infos, tag):
    """A sequential track as one strip: crossfade runs chained, then joined.

    An `xfade` chain is what a run of dissolves is, and it pulls each source
    only as the timeline reaches it. Laying the same clips onto a canvas would
    hold every decoder open for the whole edit and composite full frames
    throughout -- all to express something that is not overlapping at all.
    """
    labels = _normalised(parts, project, placements, base, infos, alpha=False)

    strips: list[str] = []
    for r, run in enumerate(_runs(placements)):
        if len(run) == 1:
            label = labels[run[0]]
        else:
            # Offsets track the running output length, as durations vary per pair.
            acc = placements[run[0]].length
            label = labels[run[0]]
            for k in range(len(run) - 1):
                nxt = run[k + 1]
                blend = placements[nxt].before.overlap
                offset = max(0.0, acc - blend)
                acc = acc + placements[nxt].length - blend
                out = f"[vx{tag}_{r}_{k}]"
                parts.append(
                    f"{label}{labels[nxt]}"
                    f"xfade=transition=fade:duration={blend}:offset={offset:.3f}{out}"
                )
                label = out

        # A dip to black either side of this run fades it down and the next up.
        chain = []
        first, last = placements[run[0]], placements[run[-1]]
        if first.before is not None and first.before.kind == "dip to black" \
                and first.before.duration > 0:
            chain.append(f"fade=t=in:st=0:d={first.before.duration:g}")
        if last.after is not None and last.after.kind == "dip to black" \
                and last.after.duration > 0:
            start = max(0.0, _run_length(placements, run) - last.after.duration)
            chain.append(f"fade=t=out:st={start:.3f}:d={last.after.duration:g}")
        if chain:
            out = f"[vgf{tag}_{r}]"
            parts.append(f"{label}{','.join(chain)}{out}")
            label = out
        strips.append(label)

    if len(strips) == 1:
        return strips[0], True
    out = f"[vseq{tag}]"
    parts.append(f"{''.join(strips)}concat=n={len(strips)}:v=1:a=0{out}")
    return out, True


def _build_video_canvas(parts, project, placements, base, infos, total, tag):
    """A track whose clips sit at times of their own, laid onto a canvas."""
    width, height = project.output.size
    fps = project.output.fps
    labels = _normalised(parts, project, placements, base, infos, alpha=True)

    canvas = f"[vc{tag}]"
    parts.append(
        f"color=c=black@0.0:s={width}x{height}:r={fps}:d={total:.6f},"
        f"format=yuva420p,setsar=1{canvas}"
    )

    for i, placement in enumerate(placements):
        if labels[i] is None:
            continue
        index = base + i
        chain = []
        before, after = placement.before, placement.after
        if before is not None and before.overlap > 0:
            # Ramping this clip's alpha up over the one below it is what a
            # dissolve is once clips are placed rather than chained.
            chain.append(f"fade=t=in:st=0:d={before.overlap:g}:alpha=1")
        elif before is not None and before.kind == "dip to black" and before.duration > 0:
            chain.append(f"fade=t=in:st=0:d={before.duration:g}")
        if after is not None and after.kind == "dip to black" and after.duration > 0:
            start = max(0.0, placement.length - after.duration)
            chain.append(f"fade=t=out:st={start:.3f}:d={after.duration:g}")
        # Shift the clip's timestamps rather than manufacturing transparent
        # frames to fill the gap: at HD a few seconds of them is thousands of
        # full frames generated only to be discarded.
        if placement.start > 0:
            chain.append(f"setpts=PTS+{placement.start:.6f}/TB")
        chain.append("format=yuva420p")

        parts.append(f"{labels[i]}{','.join(chain)}[vp{index}]")
        parts.append(
            f"{canvas}[vp{index}]"
            f"overlay=eof_action=pass:repeatlast=0:"
            f"enable='between(t,{placement.start:.6f},{placement.end:.6f})'[vo{index}]"
        )
        canvas = f"[vo{index}]"

    return canvas, False


def _build_video_track(parts, project, track, placements, base, infos, total, tag):
    """One track's picture, and whether it is already opaque and full length."""
    if is_sequential(placements):
        return _build_video_strip(parts, project, placements, base, infos, tag)
    return _build_video_canvas(parts, project, placements, base, infos, total, tag)


# ---------------------------------------------------------------- audio

def _build_audio_track(parts, project, track, placements, base, infos, total, tag):
    """Place a track's sound and mix it into one stream, or None if silent."""
    if is_sequential(placements) and rides_the_picture(placements):
        body = _build_audio_strip(parts, project, placements, base, infos, tag)
        # A strip is exactly as long as its picture, so nothing can overrun.
        return _finish_audio(parts, track, body, total, tag, runs_long=False)

    layout = audio_handles(track, placements, infos, base)
    placed: list[str] = []

    for i, placement in enumerate(placements):
        index = base + i
        info = infos[index]
        item = layout[i]
        if not info.has_audio or item["duration"] <= 0:
            continue

        # No `full_length` here, so the trim is emitted even when the clip is
        # kept whole. Placed audio is cut to an exact span before it is
        # resampled; skipping that leaves the resampler the source's own ragged
        # tail, which is a fraction of a millisecond of difference at the very
        # end of the edit -- inaudible, but not the same file.
        source = _trim_concat(parts, f"[{index}:a]", index, item["intervals"], "a")

        chain = ["aresample=48000"]
        chain += _clip_effects(placement.clip, "audio", item["duration"])
        chain.append(_pin(item["duration"]))
        if item["fade_in"] > 0:
            curve = f":curve={item['curve_in']}" if item["curve_in"] else ""
            chain.append(f"afade=t=in:st=0:d={item['fade_in']:.3f}{curve}")
        if item["fade_out"] > 0:
            start = max(0.0, item["duration"] - item["fade_out"])
            curve = f":curve={item['curve_out']}" if item["curve_out"] else ""
            chain.append(
                f"afade=t=out:st={start:.3f}:d={item['fade_out']:.3f}{curve}"
            )
        chain.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
        # Placed to the sample, not the millisecond. `adelay` defaults to whole
        # milliseconds, which at 48 kHz leaves each clip up to 24 samples off
        # its picture -- inaudible on its own, but it is a step discontinuity at
        # every boundary, and it is why placed audio never quite null-tested
        # against a plain concatenation. The `S` suffix asks for samples.
        offset = int(round(item["start"] * SAMPLE_RATE))
        if offset:
            chain.append(f"adelay={offset}S|{offset}S")

        parts.append(f"{source}{','.join(chain)}[ap{index}]")
        placed.append(f"[ap{index}]")

    if not placed:
        return None

    if len(placed) == 1:
        body = placed[0]
    else:
        parts.append(
            f"{''.join(placed)}amix=inputs={len(placed)}:normalize=0:"
            f"dropout_transition=0[am{tag}]"
        )
        body = f"[am{tag}]"
    ends = max((i["start"] + i["duration"] for i in layout), default=0.0)
    return _finish_audio(parts, track, body, total, tag,
                         runs_long=ends > total + 1e-6)


def _finish_audio(parts, track, body, total, tag, runs_long: bool = True):
    """A track's own level, held to the length of the finished video.

    Padding is unconditional -- a track shorter than the edit has to hold its
    silence out to the end. Trimming is not: it only matters for a track that
    outlasts the picture, such as a music bed, and cutting a stream that already
    ends on time still rewrites its final partial frame.
    """
    chain = []
    if abs(track.gain_db) > 1e-9:
        chain.append(f"volume={track.gain_db:g}dB")
    chain.append(f"apad=whole_dur={total:.6f}")
    if runs_long:
        chain.append(f"atrim=end={total:.6f}")
    out = f"[atk{tag}]"
    parts.append(f"{body}{','.join(chain)}{out}")
    return out


# ---------------------------------------------------------------- the graph

def build_filter_complex(project: Project, infos: list[ClipInfo]):
    """The complete filter_complex, with the output video and audio labels."""
    width, height = project.output.size
    fps = project.output.fps
    total = project_duration(project, infos)
    lengths = clip_lengths(project, infos)
    bases = input_bases(project)

    parts: list[str] = []
    video_layers: list[str] = []
    audio_layers: list[str] = []

    for position, track in enumerate(project.tracks):
        placements = track.laid_out(lengths[position])
        if not placements:
            continue
        tag = str(position)
        if track.kind is TrackKind.VIDEO and not track.hidden:
            video_layers.append(
                _build_video_track(parts, project, track, placements,
                                   bases[position], infos, total, tag)
            )

        if not track.muted:
            made = _build_audio_track(parts, project, track, placements,
                                      bases[position], infos, total, tag)
            if made:
                audio_layers.append(made)

    # One opaque track covering the whole timeline *is* the picture; putting it
    # over a black frame would only cost a compositing pass to change nothing.
    # Anything else -- layers, gaps, transparency -- lands on black, so a
    # letterboxed clip's bars and a hole between clips both come out black
    # rather than undefined.
    if len(video_layers) == 1 and video_layers[0][1]:
        parts.append(f"{video_layers[0][0]}setsar=1[vpre]")
    else:
        parts.append(
            f"color=c=black:s={width}x{height}:r={fps}:d={total:.6f},"
            f"format=yuv420p,setsar=1[vbase]"
        )
        stage = "[vbase]"
        for i, (layer, _) in enumerate(video_layers):
            parts.append(f"{stage}{layer}overlay=eof_action=pass:repeatlast=0[vl{i}]")
            stage = f"[vl{i}]"
        parts.append(f"{stage}format=yuv420p,setsar=1[vpre]")
    v_label = "vpre"

    if not audio_layers:
        parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={total:.6f},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[apre]"
        )
    elif len(audio_layers) == 1:
        parts.append(f"{audio_layers[0]}anull[apre]")
    else:
        parts.append(
            f"{''.join(audio_layers)}amix=inputs={len(audio_layers)}:"
            f"normalize=0:dropout_transition=0[apre]"
        )
    a_label = "apre"

    # Levelling sets clips by programme loudness and ignores peaks, so a boosted
    # transient could otherwise run past full scale. Catching it here costs one
    # filter and leaves everything under the limit alone.
    a_final: list[str] = []
    v_final: list[str] = []
    if project.balance.enabled:
        a_final.append(f"alimiter=limit={OUTPUT_CEILING:.4f}:level=disabled")

    in_fade = project.defaults.fade_in
    out_fade = project.defaults.fade_out
    if in_fade > 0:
        v_final.append(f"fade=t=in:st=0:d={in_fade}")
        a_final.append(f"afade=t=in:st=0:d={in_fade}")
    if out_fade > 0:
        start = max(0.0, total - out_fade)
        v_final.append(f"fade=t=out:st={start:.3f}:d={out_fade}")
        a_final.append(f"afade=t=out:st={start:.3f}:d={out_fade}")

    if v_final:
        parts.append(f"[{v_label}]{','.join(v_final)}[vout]")
        v_label = "vout"
    if a_final:
        parts.append(f"[{a_label}]{','.join(a_final)}[aout]")
        a_label = "aout"

    return ";".join(parts), v_label, a_label
