"""Building the one filter_complex that is the whole edit.

Every clip is an input to a single ffmpeg call and this is what wires them
together: dead space dropped in-graph, sizes and rates normalised, crossfade
runs blended, groups concatenated, levels and fades applied. Nothing here runs
ffmpeg -- it only writes the graph, which makes it the piece worth reading when
an edit comes out wrong.
"""

from .probe import ClipInfo
from .timeline import Join, TimelineClip, VideoScript, expand_regions

# -1 dBFS, as linear amplitude, which is what alimiter wants.
OUTPUT_CEILING = 10 ** (-1.0 / 20)

# Equal power, the curve an editor expects of an audio crossfade. Two unrelated
# signals ramped linearly sum to about -3 dB where they meet, so a long linear
# crossfade audibly sags in the middle; a quarter-sine pair holds the level flat
# because sin^2 + cos^2 = 1. Inaudible across a 0.3 s join, obvious across ten
# seconds -- which is the length an `audio overlap` join tends to be.
CROSSFADE_CURVE = "qsin"


def split_into_groups(clips: list[TimelineClip]) -> list[list[int]]:
    """Group clips into runs joined by crossfade; cut and fade joins end a run."""
    groups: list[list[int]] = []
    current: list[int] = []

    for i, clip in enumerate(clips):
        if i > 0 and clip.join is not Join.CROSSFADE and current:
            groups.append(current)
            current = []
        current.append(i)

    if current:
        groups.append(current)

    return groups


def group_duration(
    indices: list[int], infos: list[ClipInfo], clips: list[TimelineClip]
) -> float:
    """Output duration of a group, accounting for xfade overlap."""
    total = infos[indices[0]].effective_duration
    for i in indices[1:]:
        total += infos[i].effective_duration - clips[i].join_duration
    return total


def total_duration(
    groups: list[list[int]], infos: list[ClipInfo], clips: list[TimelineClip]
) -> float:
    return sum(group_duration(g, infos, clips) for g in groups)


def video_offsets(
    infos: list[ClipInfo], clips: list[TimelineClip], groups: list[list[int]]
) -> list[float]:
    """Output time each clip's picture starts at.

    The same arithmetic the xfade offsets use, pulled out so the audio layout
    cannot drift from the picture it is placed against.
    """
    offsets = [0.0] * len(clips)
    base = 0.0
    for indices in groups:
        acc = base
        offsets[indices[0]] = acc
        for k in range(1, len(indices)):
            acc += infos[indices[k - 1]].effective_duration - clips[indices[k]].join_duration
            offsets[indices[k]] = acc
        base += group_duration(indices, infos, clips)
    return offsets


def uses_audio_offsets(clips: list[TimelineClip]) -> bool:
    """True when any join asks its sound to depart from its picture."""
    return any(not clip.audio_follows_picture() for clip in clips[1:])


def audio_layout(
    infos: list[ClipInfo], clips: list[TimelineClip], groups: list[list[int]]
) -> list[dict]:
    """Where each clip's audio sits, what it plays, and how its edges blend.

    A join's audio transition is a crossfade of `audio_blend` seconds whose
    centre sits `audio_lead` seconds before the picture cut. Both are paid for
    out of the clips' own source either side of their in and out points -- so
    the timeline never shifts, the total length is still the picture's, and
    only the join you asked about loses lock with the picture.

        head = lead + (blend - join_duration) / 2   extra source before the in point
        tail = (blend - join_duration) / 2 - lead   extra source after the out point

    With no lead and a blend matching the picture, both are zero and every clip
    lands exactly where the concat/acrossfade chain would have put it.
    """
    starts = video_offsets(infos, clips, groups)
    count = len(clips)
    ranges = [info.keep_intervals or [(0.0, info.duration)] for info in infos]

    head = [0.0] * count
    tail = [0.0] * count
    # Fades are tracked per side because a shortfall on one end of a join does
    # not imply one on the other.
    fade_in = [0.0] * (count + 1)
    fade_out = [0.0] * (count + 1)
    short = [None] * count   # per join: (asked, granted) when the source ran out

    for i in range(1, count):
        overlap_ahead = clips[i].overlap
        span = 0.0 if overlap_ahead else clips[i].join_duration
        blend_len = clips[i].blend_length()
        half = (blend_len - span) / 2
        # An `audio overlap` join's whole length is the overlap: the sound
        # starts that far ahead of its own picture, which the picture gave up.
        want_head = overlap_ahead + clips[i].audio_lead + (0.0 if overlap_ahead else half)
        want_tail = 0.0 if overlap_ahead else (half - clips[i].audio_lead)

        # Only what the source actually has either side, and never so much
        # that a clip is asked to give up its own middle.
        room_head = ranges[i][0][0]
        room_tail = infos[i - 1].duration - ranges[i - 1][-1][1]
        head[i] = min(max(want_head, -0.45 * infos[i].effective_duration), room_head)
        tail[i - 1] = min(
            max(want_tail, -0.45 * infos[i - 1].effective_duration), room_tail
        )
        overlap = max(0.0, span + head[i] + tail[i - 1])
        # Both sides ramp across the whole overlap unless asked for less, so an
        # `audio overlap` join arrives over its full length rather than snapping
        # in at the front.
        fade_in[i] = fade_out[i] = min(blend_len, overlap)

        # An overlap is paid for out of source either side of the cut. A clip
        # used to its last frame has none to give, and the ask quietly shrinks
        # -- so say so rather than letting the edit look broken.
        asked = span + want_head + want_tail
        if asked - overlap > 0.01:
            short[i] = (asked, overlap)

    layout = []
    for i, info in enumerate(infos):
        spans = [[a, b] for a, b in ranges[i]]
        spans[0][0] -= head[i]
        spans[-1][1] += tail[i]
        kept = [
            (max(0.0, a), min(info.duration, b))
            for a, b in spans
            if min(info.duration, b) - max(0.0, a) > 1e-6
        ]
        layout.append({
            "shortfall": short[i],
            "start": max(0.0, starts[i] - head[i]),
            "intervals": kept,
            "duration": sum(b - a for a, b in kept),
            "fade_in": fade_in[i],
            "fade_out": fade_out[i + 1],
        })
    return layout


def audio_notes(script: VideoScript, infos: list[ClipInfo]) -> list[str]:
    """Joins whose audio overlap the sources could not fully pay for."""
    clips = expand_regions(script.clips)
    if not uses_audio_offsets(clips):
        return []
    layout = audio_layout(infos, clips, split_into_groups(clips))
    notes = []
    for clip, item in zip(clips, layout):
        if not item["shortfall"]:
            continue
        asked, got = item["shortfall"]
        notes.append(
            f"{clip.label}: audio overlap {asked:.2f}s trimmed to {got:.2f}s -- "
            f"the sources either side of the cut have no more audio to give. "
            f"Trim a clip's picture back to leave some."
        )
    return notes


def _trim_concat(index: int, intervals, kind: str) -> tuple[list[str], str]:
    """split/trim/concat one stream down to the intervals worth keeping."""
    if len(intervals) == 1 and kind == "a":
        a, b = intervals[0]
        return (
            [f"[{index}:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[aone{index}]"],
            f"[aone{index}]",
        )
    n = len(intervals)
    outs = "".join(f"[asr{index}_{j}]" for j in range(n))
    parts = [f"[{index}:a]asplit={n}{outs}"]
    trimmed = []
    for j, (a, b) in enumerate(intervals):
        parts.append(
            f"[asr{index}_{j}]atrim=start={a:.3f}:end={b:.3f},"
            f"asetpts=PTS-STARTPTS[atr{index}_{j}]"
        )
        trimmed.append(f"[atr{index}_{j}]")
    parts.append(f"{''.join(trimmed)}concat=n={n}:v=0:a=1[acat{index}]")
    return parts, f"[acat{index}]"


def _place_audio(
    infos: list[ClipInfo],
    clips: list[TimelineClip],
    groups: list[list[int]],
    script: VideoScript,
    total: float,
) -> tuple[list[str], str]:
    """Build the audio as placed, mixed segments rather than one concat chain.

    Only used when a join actually asks for it. The ordinary path stays a
    concat/acrossfade chain: it is what every existing project renders through,
    it pulls each source only when the timeline reaches it, and this mix is
    strictly more machinery for the same result when nothing is offset.
    """
    layout = audio_layout(infos, clips, groups)
    parts: list[str] = []
    placed: list[str] = []

    for i, (info, item) in enumerate(zip(infos, layout)):
        # A silent clip contributes silence; leaving it out of the mix says
        # the same thing for less work.
        if not info.has_audio or item["duration"] <= 0:
            continue

        made, source = _trim_concat(i, item["intervals"], "a")
        parts += made

        chain = ["aresample=48000"]
        gain = clips[i].audio_gain_db
        if abs(gain) > 1e-9:
            chain.append(f"volume={gain:g}dB")
        chain.append(_pin(item["duration"]).rstrip(","))
        if item["fade_in"] > 0:
            chain.append(
                f"afade=t=in:st=0:d={item['fade_in']:.3f}:curve={CROSSFADE_CURVE}"
            )
        if item["fade_out"] > 0:
            start = max(0.0, item["duration"] - item["fade_out"])
            chain.append(
                f"afade=t=out:st={start:.3f}:d={item['fade_out']:.3f}"
                f":curve={CROSSFADE_CURVE}"
            )
        chain.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
        # adelay works in whole milliseconds -- a fifteenth of a frame at 60fps.
        offset = int(round(item["start"] * 1000))
        if offset:
            chain.append(f"adelay={offset}|{offset}")
        parts.append(f"{source}{','.join(chain)}[ap{i}]")
        placed.append(f"[ap{i}]")

    if not placed:
        parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={total:.6f},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[apre]"
        )
        return parts, "apre"

    parts.append(
        f"{''.join(placed)}amix=inputs={len(placed)}:normalize=0:"
        f"dropout_transition=0[amixed]"
    )
    # The picture decides how long the video is; pad the mix out to match.
    parts.append(f"[amixed]apad=whole_dur={total:.6f}[apre]")
    return parts, "apre"


# -1 dBFS, as linear amplitude, which is what alimiter wants.
OUTPUT_CEILING = 10 ** (-1.0 / 20)


def _pin(duration: float) -> str:
    """Force an audio stream to exactly `duration`, padding or clipping it.

    A decoder's idea of a clip's length is not the timeline's: AAC pads its
    final frame by up to ~20 ms, and a capture can hand back an audio track
    shorter than its video. Concatenation believes whatever it is given, so
    without this each clip's slack pushes every later clip out of step with its
    own picture -- a drift that grows with the clip count rather than cancelling.
    """
    return f"apad,atrim=end={duration:.6f},asetpts=PTS-STARTPTS,"


def build_filter_complex(
    infos: list[ClipInfo],
    clips: list[TimelineClip],
    groups: list[list[int]],
    script: VideoScript,
) -> tuple[str, str, str]:
    """Build the filter_complex, returning it with the output video/audio labels."""
    width, height = script.output.size
    fps = script.output.fps
    in_fade = script.defaults.fade_in
    out_fade = script.defaults.fade_out

    parts: list[str] = []
    # When a join wants its sound off its picture, audio is placed and mixed
    # instead of chained; the per-clip audio normalising below is then skipped.
    placed = uses_audio_offsets(clips)

    # Per-clip: drop dead space in-graph, then normalise size, rate and format.
    for i, info in enumerate(infos):
        v_src, a_src = f"[{i}:v]", f"[{i}:a]"

        if info.keep_intervals:
            k = len(info.keep_intervals)
            v_outs = "".join(f"[vsr{i}_{j}]" for j in range(k))
            parts.append(f"[{i}:v]split={k}{v_outs}")
            v_trimmed = []
            for j, (a, b) in enumerate(info.keep_intervals):
                parts.append(
                    f"[vsr{i}_{j}]trim=start={a:.3f}:end={b:.3f},"
                    f"setpts=PTS-STARTPTS[vtr{i}_{j}]"
                )
                v_trimmed.append(f"[vtr{i}_{j}]")
            parts.append(f"{''.join(v_trimmed)}concat=n={k}:v=1:a=0[vcat{i}]")
            v_src = f"[vcat{i}]"

            if info.has_audio and not placed:
                a_outs = "".join(f"[asr{i}_{j}]" for j in range(k))
                parts.append(f"[{i}:a]asplit={k}{a_outs}")
                a_trimmed = []
                for j, (a, b) in enumerate(info.keep_intervals):
                    parts.append(
                        f"[asr{i}_{j}]atrim=start={a:.3f}:end={b:.3f},"
                        f"asetpts=PTS-STARTPTS[atr{i}_{j}]"
                    )
                    a_trimmed.append(f"[atr{i}_{j}]")
                parts.append(f"{''.join(a_trimmed)}concat=n={k}:v=0:a=1[acat{i}]")
                a_src = f"[acat{i}]"

        parts.append(
            f"{v_src}"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},"
            f"format=yuv420p,"
            f"setsar=1"
            f"[vn{i}]"
        )
        if placed:
            continue
        if info.has_audio:
            # This clip's own trim and what levelling worked out are one gain.
            gain = clips[i].audio_gain_db
            level = f"volume={gain:g}dB," if abs(gain) > 1e-9 else ""
            parts.append(
                f"{a_src}"
                f"aresample=48000,"
                f"{level}"
                f"{_pin(info.effective_duration)}"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo"
                f"[an{i}]"
            )
        else:
            parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000:"
                f"duration={info.effective_duration:.6f},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo"
                f"[an{i}]"
            )

    group_v: list[str] = []
    group_a: list[str] = []

    for g, indices in enumerate(groups):
        n = len(indices)

        if n == 1:
            v_label = f"vn{indices[0]}"
            a_label = f"an{indices[0]}"
        else:
            # Offsets track the running output length, as blend durations vary per pair.
            acc = infos[indices[0]].effective_duration

            for k in range(n - 1):
                nxt = indices[k + 1]
                blend = clips[nxt].join_duration
                offset = max(0.0, acc - blend)
                acc = acc + infos[nxt].effective_duration - blend

                is_last_pair = (k == n - 2)
                v_in1 = f"[vn{indices[0]}]" if k == 0 else f"[vxi{g}_{k}]"
                v_out = f"[vx{g}]" if is_last_pair else f"[vxi{g}_{k + 1}]"
                a_in1 = f"[an{indices[0]}]" if k == 0 else f"[axi{g}_{k}]"
                a_out = f"[ax{g}]" if is_last_pair else f"[axi{g}_{k + 1}]"

                parts.append(
                    f"{v_in1}[vn{nxt}]"
                    f"xfade=transition=fade:duration={blend}:offset={offset:.3f}"
                    f"{v_out}"
                )
                if not placed:
                    parts.append(
                        f"{a_in1}[an{nxt}]"
                        f"acrossfade=d={blend}"
                        f":c1={CROSSFADE_CURVE}:c2={CROSSFADE_CURVE}"
                        f"{a_out}"
                    )

            v_label = f"vx{g}"
            a_label = f"ax{g}"

        v_chain: list[str] = []
        a_chain: list[str] = []

        # A fade join opening this group fades it up from black.
        if g > 0 and clips[indices[0]].join is Join.FADE:
            up = clips[indices[0]].join_duration
            v_chain.append(f"fade=t=in:st=0:d={up}")
            a_chain.append(f"afade=t=in:st=0:d={up}")

        # A fade join opening the next group fades this one down to black.
        if g + 1 < len(groups):
            nxt_first = groups[g + 1][0]
            if clips[nxt_first].join is Join.FADE:
                down = clips[nxt_first].join_duration
                start = max(0.0, group_duration(indices, infos, clips) - down)
                v_chain.append(f"fade=t=out:st={start:.3f}:d={down}")
                a_chain.append(f"afade=t=out:st={start:.3f}:d={down}")

        if v_chain:
            parts.append(f"[{v_label}]{','.join(v_chain)}[vgf{g}]")
            v_label = f"vgf{g}"
            if not placed:
                parts.append(f"[{a_label}]{','.join(a_chain)}[agf{g}]")
                a_label = f"agf{g}"

        group_v.append(v_label)
        group_a.append(a_label)

    total = total_duration(groups, infos, clips)

    if placed:
        # Video concats on its own; audio is mixed from its placed segments.
        if len(groups) == 1:
            v_pre = group_v[0]
        else:
            joined = "".join(f"[{v}]" for v in group_v)
            parts.append(f"{joined}concat=n={len(groups)}:v=1:a=0[vpre]")
            v_pre = "vpre"
        audio_parts, a_pre = _place_audio(infos, clips, groups, script, total)
        parts += audio_parts
    elif len(groups) == 1:
        v_pre, a_pre = group_v[0], group_a[0]
    else:
        interleaved = "".join(f"[{v}][{a}]" for v, a in zip(group_v, group_a))
        parts.append(f"{interleaved}concat=n={len(groups)}:v=1:a=1[vpre][apre]")
        v_pre, a_pre = "vpre", "apre"

    v_final: list[str] = []
    a_final: list[str] = []

    # Levelling sets clips by their programme loudness and ignores peaks, so a
    # boosted clip's transients could otherwise run past full scale. Catching
    # them here costs one filter and leaves everything below the limit alone --
    # far better than holding a whole clip down because it once got loud.
    if script.balance.enabled:
        a_final.append(f"alimiter=limit={OUTPUT_CEILING:.4f}:level=disabled")

    if in_fade > 0:
        v_final.append(f"fade=t=in:st=0:d={in_fade}")
        a_final.append(f"afade=t=in:st=0:d={in_fade}")
    if out_fade > 0:
        start = max(0.0, total - out_fade)
        v_final.append(f"fade=t=out:st={start:.3f}:d={out_fade}")
        a_final.append(f"afade=t=out:st={start:.3f}:d={out_fade}")

    if v_final:
        parts.append(f"[{v_pre}]{','.join(v_final)}[vout]")
        v_pre = "vout"
    if a_final:
        parts.append(f"[{a_pre}]{','.join(a_final)}[aout]")
        a_pre = "aout"

    return ";".join(parts), v_pre, a_pre


