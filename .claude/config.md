# Configuration Reference

All config lives in `.env` (copy from `.env.example`). Loaded by `autoeditor/config.py` → `Config` dataclass.

Use forward slashes for paths on Windows. Wrap paths with spaces in double quotes.

## Asset paths

| Variable | Description |
|---|---|
| `INTRO_PATH` | Intro clip |
| `OUTRO_PATH` | Outro clip |
| `TRANSITION_PATH` | Transition clip — inserted after deck tech, before gameplay |
| `MIDROLL_AD_PATH_1` | Ad clip inserted after deck tech + transition |
| `MIDROLL_AD_PATH_2` | Ad clip inserted ~halfway through the games section |
| `MIDROLL_AD_1_ENABLED` | `true`/`false` — toggle ad 1 without removing its path |
| `MIDROLL_AD_2_ENABLED` | `true`/`false` — toggle ad 2 without removing its path |

All asset paths are optional. Unset = asset is skipped.

## Folders

| Variable | Default | Description |
|---|---|---|
| `PROJECT_FOLDER_PATH` | `./input` | The project folder itself (`PROCESS_MULTI=false`) or parent of project folders (`PROCESS_MULTI=true`) |
| `OUTPUT_FOLDER` | `./output` | Where rendered `.mp4` files are written |

## Behaviour

| Variable | Default | Description |
|---|---|---|
| `PROCESS_MULTI` | `true` | `true` = batch all subfolders in `PROJECT_FOLDER_PATH`; `false` = treat `PROJECT_FOLDER_PATH` as a single project folder |

## Output settings

| Variable | Default | Description |
|---|---|---|
| `TARGET_RESOLUTION` | `3440x2160` | Output resolution as `WxH` |
| `TARGET_FPS` | `60` | Output frame rate |
| `FADE_DURATION` | `0.3` | Crossfade duration in seconds between clips within a group. `0` = hard cuts |
| `OUTPUT_FADE_DURATION` | `0.5` | Duration in seconds for: fade-in at start of final output, fade-out at end of final output, and fade-out before each midroll ad |

## Dead Space Removal

Applies to **game segments only** (`SegmentType.GAME`). Detection is per-clip and relative to that clip's own peak loudness. Trimming is done in-graph (`trim`/`atrim` + `concat`) during the single render pass — no intermediate files.

| Variable | Default | Description |
|---|---|---|
| `REMOVE_DEAD_SPACE` | `true` | Master toggle for silence trimming on game recordings |
| `DEAD_SPACE_PADDING` | `0.5` | Seconds of buffer kept around each loud region; gaps `< 2×` this merge |
| `DEAD_SPACE_THRESHOLD_DB` | `-30` | Silence floor in dB below the clip's peak (`max_volume`). More negative = less aggressive |
| `DEAD_SPACE_MIN_SILENCE` | `1.0` | Minimum silence length (s) passed to `silencedetect` `d=` |
| `DEAD_SPACE_MIN_SEGMENT` | `0.5` | Kept regions shorter than this (s) are dropped |
