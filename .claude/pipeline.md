# Video Pipeline

The assembly order is whatever the script's `## Timeline` says. Nothing in the
code knows what an intro, a deck tech or a midroll ad is — those are just clips
with joins.

## From script to output

```
myvideo.md
  → parse_script()    → VideoScript (clips, each with a Join to the one before)
  → probe_script()    → ClipInfo per clip (+ keep_intervals where silence is trimmed)
  → _split_into_groups() → runs of crossfaded clips
  → _build_filter_complex() → one filter_complex string
  → one ffmpeg call   → output.mp4
```

## Joins

Each timeline item states how it attaches to the clip before it:

| Join | Effect |
|---|---|
| `crossfade d` | `xfade` + `acrossfade` of `d` seconds. Clips stay in the same group. |
| `cut` | Hard cut. Ends the current group. |
| `fade d` | Previous group fades to black over `d`, this one fades up over `d`, hard cut between. Ends the current group. |

A `crossfade` or `fade` of `0` is collapsed to a `cut` at parse time, so the
filter graph never contains a zero-length blend.

## Grouping

A **group** is a maximal run of clips joined by `crossfade`. Groups exist
because an xfade chain has to be built as one connected filter chain, while
`cut` and `fade` boundaries are plain concatenation.

```
intro  deck-tech   ad     transition  game-1   transition  game-2   outro
      ×crossfade  ×fade  ×cut        ×crossfade ×crossfade ×crossfade ×fade
└──── group 0 ────┘└ g1 ┘└──────────── group 2 ────────────┘└─ g3 ─┘
```

## Rendering steps

0. **Dead-space detection** (for clips marked `trim silence`) — measure peak loudness, run `silencedetect` with a floor relative to that peak, invert to loud regions, pad + merge + drop tiny → `keep_intervals`. Cached per source file.
1. **Normalize** every clip — target resolution (letterboxed via `scale`+`pad`), target fps, `yuv420p`, `setsar=1`; audio to 48 kHz stereo fltp. Clips with `keep_intervals` are `split`/`trim`/`concat`ed first so the silence is gone before normalisation. Clips with no audio track get an `anullsrc` silent track of matching length.
2. **Crossfade within each group** — an `xfade`/`acrossfade` chain. Each pair may use a different duration.
3. **Fade at group boundaries** — a `fade` join adds `fade=out`/`afade=out` to the group before it and `fade=in`/`afade=in` to the group after.
4. **Concat all groups** — hard cuts, in one `concat` filter.
5. **Final fades** — the output's `fade in` / `fade out` on the fully assembled stream.

Steps 0–5 are a single ffmpeg invocation; only step 0 runs separate (audio-only, no video decode) passes beforehand.

## xfade offset formula

Durations can differ per join, so the offset is tracked as a running total of
the chain's own output length rather than a closed form:

```
acc = duration[0]
for each following clip i with join duration d:
    offset = acc - d
    acc    = acc + duration[i] - d
```

`acc` at the end is the group's output duration, which is also what the
group-boundary fade-out position and the total-duration progress bar use.
