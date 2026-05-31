# Video Pipeline

## Assembly order (N games)

```
Intro → Deck Tech → Midroll Ad 1 → Transition
      → Game 1 → T → ... → Game ⌈N/2⌉  [fade out]
      → Midroll Ad 2
      → Game ⌈N/2⌉+1 → T → ... → Game N
      → Outro
```

## Rules

- Transition plays **after Midroll Ad 1**, immediately before Game 1.
- No transition before Midroll Ad 1, or on either side of Midroll Ad 2 — always hard cuts.
- The content group immediately before each midroll ad fades out to black (`OUTPUT_FADE_DURATION`) before the hard cut.
- Midroll Ad 2 is only inserted when there are ≥ 2 games; it splits the games at `ceil(num_games / 2)`.
- Any asset with no path set or `_ENABLED=false` is silently skipped.
- Global fade-in at the very start and fade-out at the very end of the final output.

## Rendering steps ()

0. **Dead-space detection** (preprocess, `REMOVE_DEAD_SPACE=true`) — for each `GAME` clip, measure peak loudness and run `silencedetect` (floor relative to peak), invert to loud regions, pad + merge + drop tiny → `keep_intervals`. In-graph `trim`/`atrim` + `concat` drops the silence during normalization (step 1); no intermediate files.
1. **Normalize** all clips — h264/aac 48kHz stereo, target resolution (letterboxed), target FPS. Silent audio synthesised if source has no audio track.
2. **Split into groups** — midroll ads are isolated as single-item groups; all other consecutive segments form content groups.
3. **Render each group:**
   - 2+ clips + `FADE_DURATION > 0`: chain `xfade` (video) + `acrossfade` (audio)
   - 1 clip or `FADE_DURATION=0`: stream-copied directly
   - If next group is a midroll: apply `apply_fade_out` pass on this group's rendered output
4. **Hard-cut concat** all group outputs via concat demuxer (`-c copy`)
5. **Final fades** — `fade`/`afade` in+out on the fully assembled file

## xfade offset formula

For clip `i` in a chain of N clips with crossfade duration `d`:

```
offset_i = sum(durations[0..i]) - (i + 1) * d
```
