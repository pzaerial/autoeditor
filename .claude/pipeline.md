# Video Pipeline

The edit is a set of **tracks**, each an ordered list of **clips** and the
**transitions** between them. Nothing in the code knows what an intro, a deck
tech or a midroll ad is — those are clips on a track.

## From script to output

```
myvideo.md
  → parse_project()        → Project (tracks of clips and transitions)
                             an older `## Timeline` script is migrated here
  → probe_project()        → ClipInfo per clip (+ keep_intervals, head trims)
  → build_filter_complex() → one filter_complex for the whole edit
  → one ffmpeg call        → output.mp4
```

## Where a clip sits

`Track.laid_out()` is the only place this is decided, and the app's `layout()`
in `ui/js/state.js` applies the same rule — the timeline would otherwise draw
something the render does not produce.

> Clips are sequential: each starts where the last ended, minus the overlap of
> the transition joining them. A clip with an explicit `start` anchors there and
> the chain resumes from it.

That is what keeps an ordinary cut list free of absolute times while still
allowing an overlay, a title or a music bed to sit anywhere.

## The library

`effects.py` declares every effect and transition once: name, aliases,
parameters, and — for a transition — how it sits on the timeline. Four
properties decide everything downstream:

| Property | Meaning | True for |
|---|---|---|
| `overlaps` | the two clips overlap for the transition's length | `crossfade` |
| `trims_incoming` | the incoming clip gives up that much *picture* from its head | `audio overlap` |
| `audio_mode` | `crossfade` (the two swap over) or `under` (the incoming plays beneath) | `under` for `audio overlap` |
| `audio_curve` | equal power, or ffmpeg's default straight line | `""` for `dip to black` |

`audio overlap` is not a special case in the compiler: it is a transition that
trims the incoming picture and whose sound plays *under* rather than swapping.
`dip to black` fades each side to real silence, so it takes a linear curve —
equal power is only right when two signals overlap and sum.

## Building the graph

Per track, `compositor.py` picks between two shapes:

- **A strip** — the track is one unbroken run of clips with no gaps and no
  pinned starts. Crossfade runs become `xfade`/`acrossfade` chains, dip-to-black
  boundaries get `fade`/`afade`, and the runs concatenate. This pulls each
  source only as the timeline reaches it.
- **A canvas** — anything else: layers, gaps, pinned clips. Each clip is shifted
  with `setpts`/`adelay` and composited with `overlay`/`amix`.

Both express the same model; the strip exists because the canvas holds every
decoder open for the whole edit. On a long HD timeline that was enough to
starve the mix — `amix` reached the end of the clips it could see and finished
early, leaving everything after the first clip silent.

Audio takes the same fork, and additionally uses the canvas whenever a
transition asks its sound to leave its picture (`rides_the_picture`).

Video tracks are overlaid bottom-up onto one opaque black frame; audio tracks
are mixed. A single opaque full-length track skips that pass — it *is* the
picture already.

## Audio timing across a join

`audio_handles` works out how much source each side borrows:

```
crossfade mode:  head = lead + (blend - span) / 2
                 tail = (blend - span) / 2 - lead
under mode:      head = duration + lead
                 tail = 0
```

`span` is the transition's duration, except where the picture already gave that
time up at the head (`audio overlap`), where it is 0. Both are clamped to what
the source actually has outside the in and out points, and to 45% of a clip so
nothing is asked to give up its middle. A shortfall is reported by
`audio_notes`.

`_pin` forces each clip's audio to exactly the length its picture occupies. A
decoder's idea of a clip's length is not the timeline's — AAC pads its final
frame by up to ~20 ms — and without this each clip's slack pushes the next out
of step with its own picture.

## Rendering steps

0. **Dead-space detection** for clips carrying `trim silence`, scoped to the
   spans the edit keeps. Cached per source *and* span.
1. **Per clip** — drop dead space in-graph, then `scale`/`pad`/`fps`/`setsar`;
   audio to 48 kHz stereo. Effects emit into this chain, in the order the clip
   lists them.
2. **Per track** — strip or canvas, as above.
3. **Composite** — overlay video tracks, mix audio tracks, apply per-track gain.
4. **Final** — the limiter when levelling is on, then the output's own fades.

All of it is a single ffmpeg invocation; only step 0 runs separate audio-only
passes beforehand.
