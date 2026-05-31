# Architecture

```
main.py                  — entry point, calls cli()
.env / .env.example      — all configuration
autoeditor/
    config.py            — load_config() → Config dataclass from .env
    project.py           — scan_project() → ProjectFolder (deck_tech + games list)
    pipeline.py          — build_pipeline() → list[Segment] in assembly order
    silence.py           — compute_keep_intervals() → loud regions for dead-space removal
    ffmpeg_ops.py        — all ffmpeg work: probe, normalize, xfade, concat, fades
    cli.py               — click CLI: run / process / batch commands
```

## config.py
`load_config()` reads `.env` and returns a typed `Config` dataclass. Asset paths are `Path | None` — unset = skipped.

## project.py
`scan_project(folder)` sorts video files by mtime then name. First file = **deck tech**, rest = **games**.
`scan_projects_folder(root)` iterates subdirectories for batch mode.

Project folders are named `YYYY.MM.DD-deck-name` (e.g. `2026.03.13-jeskai-control`).

## pipeline.py
`build_pipeline(project, config)` returns `list[Segment]`. Each `Segment` has a `SegmentType` enum (`INTRO`, `OUTRO`, `TRANSITION`, `DECK_TECH`, `GAME`, `MIDROLL_AD`) and a `Path`.
`describe_pipeline()` returns a human-readable string used by `--dry-run`.

## silence.py
`compute_keep_intervals(path, duration, config)` → `list[(start,end)] | None` (None = keep whole clip).
Two audio-only ffmpeg passes per clip: `volumedetect` (peak `max_volume`) then `silencedetect` with `noise = peak + DEAD_SPACE_THRESHOLD_DB`. Silences are inverted into loud regions, padded, merged (gaps `< 2×padding`), and tiny segments dropped. Called from `render_project` for `GAME` segments only.

## ffmpeg_ops.py
Single-pass architecture — all source clips are inputs to one ffmpeg call. No intermediate files.

- `probe_clip(path)` → `ClipInfo` (duration, resolution, fps, has_audio)
- `_split_into_groups(segments)` → groups segments at midroll ad boundaries; each midroll is isolated as a single-item group
- `_group_duration(indices, clips, fade_dur)` → effective output duration of a group after xfade overlap
- `render_project` also runs dead-space detection (via `silence.py`) for `GAME` clips, storing `keep_intervals` on `ClipInfo`. `ClipInfo.effective_duration` reflects the post-trim length and is used everywhere durations feed xfade offsets / fades / progress.
- `_build_filter_complex(clips, groups, config)` → builds the complete filter_complex string:
  - Per-clip: when `keep_intervals` is set, `split → trim/atrim each interval → concat` to drop dead space in-graph; then `scale/pad/fps/format` for video; `aresample/aformat` for audio; `aevalsrc` for clips with no audio track
  - Per-group: `xfade`+`acrossfade` chain (or hard `concat` if `FADE_DURATION=0`); `fade=out`+`afade=out` on groups before midrolls
  - Final: `concat` across all groups, then `fade=in/out`+`afade=in/out` on the assembled output
- `render_project(segments, output, config)` → probes all clips, calls `_build_filter_complex`, runs one ffmpeg command

## cli.py
Three commands, all support `--dry-run`:
- `run` — reads `PROCESS_MULTI` from `.env`, routes to single or batch automatically
- `process FOLDER` — explicit single project folder
- `batch FOLDER` — explicit multi-project root folder
