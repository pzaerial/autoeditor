# Architecture

```
script.py                — entry point: python script.py myvideo.md
myvideo.md               — the edit: paths, order, format (see script-format.md)
autoeditor/
    timeline.py          — data model: VideoScript, TimelineClip, Join, settings
    script_parser.py     — parse_script() → VideoScript from a markdown file
    silence.py           — compute_keep_intervals() → loud regions for dead-space removal
    ffmpeg_ops.py        — all ffmpeg work: probe, normalize, xfade, concat, fades
```

There is no `.env`, no click CLI and no hard-coded assembly order — the markdown script is the only input.

## timeline.py
Pure data, no logic. `VideoScript` holds `OutputSettings`, `SilenceSettings`, `Defaults` and an ordered `list[TimelineClip]`.

Each `TimelineClip` carries the `Join` describing how it attaches to the clip **before** it (`CUT`, `CROSSFADE`, `FADE`) plus that join's duration, so the whole edit is a flat list. `VideoScript.describe()` renders the summary printed before a render.

## script_parser.py
`parse_script(path)` → `VideoScript`, raising `ScriptError` (which carries a line number) on any problem.

Only two line shapes are meaningful: `#` headings open sections, and list items carry values. Everything else — prose, blockquotes, fenced blocks — is skipped, so scripts double as episode notes. Unknown sections warn; unknown keys and options are hard errors listing the valid ones.

Section aliases live in `_SECTION_ALIASES`; key aliases in `_OUTPUT_KEYS` / `_SILENCE_KEYS` / `_DEFAULT_KEYS`. Keys are normalised by `_norm_key` so `Fade In`, `fade_in` and `**fade-in**` all match.

`_build_clips` resolves each timeline item: asset aliases → paths, relative paths → against the script's own folder, then `_expand_source` turns folders and globs into their video files (sorted by mtime then name). A `crossfade`/`fade` of zero length collapses to `CUT` here, so the filter graph never sees a degenerate blend.

## silence.py
`compute_keep_intervals(path, duration, settings)` → `list[(start,end)] | None` (None = keep whole clip).
Two audio-only ffmpeg passes per clip: `volumedetect` (peak `max_volume`) then `silencedetect` with `noise = peak + threshold_db`. Silences are inverted into loud regions, padded, merged (gaps `< 2×padding`), and tiny segments dropped. Called from `probe_script` for clips marked `trim silence`.

## ffmpeg_ops.py
Single-pass architecture — all source clips are inputs to one ffmpeg call. No intermediate files.

- `probe_clip(path)` → `ClipInfo` (duration, resolution, fps, has_audio)
- `probe_script(script)` → `list[ClipInfo]`, running silence detection where the script asks for it. Results are cached per source path, so a clip reused across the timeline is analysed once. `ClipInfo.effective_duration` reflects the post-trim length and feeds every xfade offset, fade position and progress total.
- `_split_into_groups(clips)` → runs of clips joined by `CROSSFADE`; `CUT` and `FADE` joins end a group
- `_group_duration(indices, infos, clips)` → a group's output length after xfade overlap (per-join durations, not one global value)
- `_build_filter_complex(infos, clips, groups, script)` → the complete filter_complex string:
  - Per-clip: when `keep_intervals` is set, `split → trim/atrim each interval → concat` to drop dead space in-graph; then `scale/pad/fps/format/setsar` for video, `aresample/aformat` for audio, `anullsrc` for clips with no audio track
  - Per-group: `xfade`+`acrossfade` chain across the group's clips
  - At `FADE` boundaries: `fade=out`/`afade=out` on the group before, `fade=in`/`afade=in` on the group after
  - Final: `concat` across all groups, then the output's own `fade=in/out`+`afade=in/out`
- `render_script(script, infos)` → builds the graph and runs one ffmpeg command with live progress

## script.py
Prints the resolved script, honours `dry run`, checks ffmpeg/ffprobe are on PATH, then probes and renders. Reports `ScriptError` with the offending line and prints the tail of ffmpeg's stderr if the encode fails.
