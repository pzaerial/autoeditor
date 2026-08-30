# FullControl MTG – Auto Editor
Markdown driven video autoeditor written in Python.
```
python script.py myvideo.md
```
Control your video format by writing a markdown file.

Or use the desktop app, which does the same thing with a preview and a timeline:
```
npm install      (once)
npm start
```
Both drive the same engine — the app exports its project as a markdown script, and
`python script.py` renders that export identically. Full guide: [.claude/ui.md](.claude/ui.md).

## Requirements
- Python 3.10+ (no third-party packages)
- [ffmpeg](https://ffmpeg.org/download.html) and `ffprobe` on your `PATH`
- Node 18+ **only** for the desktop app; the CLI and the browser UI need neither

## Quick Start
Copy a script out of [templates/](templates/), point the paths at your own files, and run it:
```
python script.py templates\example.md
```
Set `dry run: yes` in the `## Output` section to print the resolved timeline without rendering.


## Tempates

Templates are written in markdown. Example:

```markdown
# 2026.03.13 Jeskai Control

## Output
- file: C:\Users\you\Videos\out\jeskai.mp4
- resolution: 1920x1080
- fps: 60

## Defaults
- crossfade: 0.3
- trim silence: no

## Assets
- intro: C:\Users\you\Videos\assets\intro.mp4
- transition: C:\Users\you\Videos\assets\transition.mp4
- ad 1: C:\Users\you\Videos\assets\midroll_1.mp4

## Timeline
1. `intro`
2. `C:\Users\you\Videos\raw\jeskai\deck-tech.mp4` -- trim silence
3. `ad 1` -- fade
4. `transition` -- cut
5. `C:\Users\you\Videos\raw\jeskai\games` -- trim silence
6. `intro` -- fade
```

Full reference: [.claude/script-format.md](.claude/script-format.md).

### Sections

| Section | Holds |
|---|---|
| `# Title` | episode name; cosmetic |
| `## Output` | destination file and format |
| `## Joins` | how clips meet each other, and the black at either end |
| `## Auto Editor` | the opt-in passes: levelling and silence trimming |
| `## Assets` | named shortcuts for reused clips |
| `## Timeline` | the running order — required |

Only `## Timeline` is required. Keys are case- and punctuation-insensitive
(`Fade In` = `fade_in` = `fade-in`), and a setting is understood wherever it
appears — so an older file using `## Defaults` and `## Silence` still opens.
Saving it from the app rewrites it in the layout above.

The full reference, generated from the one place the settings are declared, is in
[.claude/script-format.md](.claude/script-format.md).

### Output

| Key | Default | Value |
|---|---|---|
| `file` | *required* | `.mp4` to write; parent folders created |
| `resolution` | `1920x1080` | `WxH`; sources letterboxed to fit |
| `fps` | `60` | output frame rate |
| `encoder` | `libx264` | see encoders below |
| `quality` | per encoder | lower = bigger and better (CRF-like everywhere) |
| `dry run` | `no` | `yes` = print timeline, don't render |

| Encoder | Hardware |
|---|---|
| `libx264`, `libx265` | CPU |
| `h264_nvenc`, `hevc_nvenc` | NVIDIA |
| `h264_amf` | AMD |
| `h264_qsv` | Intel Quick Sync |

Audio is always AAC 192k, 48 kHz, stereo.

**Rendering on the GPU:** set `encoder` to `h264_nvenc` (NVIDIA), `h264_amf` (AMD) or
`h264_qsv` (Intel). Measured on a three-clip 1080p60 edit: `libx264` 12.4s, `h264_nvenc`
5.5s — about twice as fast. The app greys out hardware encoders this machine cannot use.

High CPU with an apparently idle GPU is expected, and mostly a measurement artefact.
NVENC is fixed-function silicon separate from the shaders, so `utilization.gpu` stays in
the low teens while the encoder engine runs at 100% — the render page graphs both, and the
encoder trace is the one that matters. The CPU stays busy for a real reason: decoding every
input and running the transitions is CPU work whatever encodes the result, and that stage
alone was 3.9s of those 5.5s.

### Timeline

```
<source> -- <option>, <option>, ...
```

`<source>` = file, folder, glob or asset name. Separator: `--`, `—`, or `|`.
Options apply to the join with the **previous** item; the first item's join is ignored.

| Option | Effect |
|---|---|
| `cut` | hard cut |
| `crossfade [s]` | blend with previous |
| `fade [s]` | previous to black, this up from black |
| `audio overlap [s]` | heard before it is seen: sound starts early, and that much picture is dropped so the two stay locked |
| `trim silence` | remove dead air |
| `keep silence` | override a `trim silence` default |
| `volume [dB]` | level trim for this clip; `gain` and `audio` are aliases |
| `audio blend [s]` | length of this join's audio transition; `auto` follows the picture |
| `audio lead [s]` | seconds the audio transition happens before the picture cut |
| `2:10-5:30` | keep only this range; repeat for more |

A join written **after** a range applies to the range that follows it, so sections of one
clip can blend into each other:

```markdown
6. `C:\Footage\stream.mp4` -- fade, 2:10-5:30, crossfade 0.5, 41:00-52:20
```

`fade` = how the clip joins the previous clip. `crossfade 0.5` = how the second range joins
the first. Ranges must not overlap.

```markdown
1. `intro`
2. `C:\Footage\ep12\game-1.mp4` -- crossfade 0.5, trim silence
3. `C:\Assets\ad.mp4` -- fade
4. `C:\Footage\stream.mp4` -- 2:10-5:30, 41:00-52:20, trim silence
```

Ranges: `SS`, `MM:SS` or `HH:MM:SS`, decimals allowed. No range = whole clip.
Clamped to real duration. End must follow start. Combine with `trim silence` = silence
removed from within the kept ranges. Written by the app's Edit page.
`crossfade 0` and `fade 0` = `cut`.

### Sound across a join

`audio blend` and `audio lead` let the sound change hands somewhere other than
the picture cut — a music bed carried over a hard cut, a J-cut, an L-cut:

```markdown
4. `C:\Assets\outro.mp4` -- cut, audio blend 3, audio lead 1
```

The overlap is played from the clips' own source either side of the cut, so the
timeline never shifts and no gap appears. A clip used to its last frame has no
spare audio to overlap with — trim its picture back to leave some, and the
render will tell you how much it could actually use.

### Paths

| Rule | |
|---|---|
| Windows paths | as-is: `C:\Users\you\Videos\clip.mp4` |
| Spaces | no escaping or quoting; same for `#` `&` `'` `!` |
| Relative | resolved against the `.md` file's folder |
| Backticks / quotes | optional; needed only if the path contains `--` |
| `: < > " \| ? *` | invalid in a Windows name; output path rejected up front |
| `%VAR%`, `$VAR`, `~` | expanded |
| Folder | expands to every video inside, by mtime then name |
| Glob | `raw\ep12\game-*.mp4`, expands the same way |

Expanded clips all take the item's join and options, including between themselves.
Inputs: `.mp4` `.mov` `.mkv` `.avi` `.m4v` `.webm` `.wmv` `.flv`. Output: h264/aac `.mp4`.

---

## How it renders

One ffmpeg pass, no intermediate files.

1. Normalise each clip — scale + letterbox, target fps, `yuv420p`, audio 48 kHz stereo,
   then its `volume` trim plus whatever levelling worked out, as one gain
2. `trim silence` clips: `volumedetect` for peak, `silencedetect` relative to it, trimmed in-graph — analysed over **only the ranges the edit keeps**, which is both quicker and better targeted than measuring a whole stream you are cutting most of
3. `crossfade` runs blended with `xfade` / `acrossfade`, each clip's audio
   pinned to its own length so nothing drifts against the picture
4. `cut` and `fade` joins hard-cut; `fade` dips both sides to black first
5. Final fade in / out on the assembled output

Progress is reported live.

---

## Notes

- A clip may appear any number of times; silence analysis is cached per file
- Clips with no audio track get a silent one synthesised
- `crossfade 0` = `cut`

---

## Preview

The app previews clips in a browser window, which decodes less than ffmpeg does. `.avi`
cannot be opened at all, and HEVC needs hardware decoding — Chromium has no software HEVC
decoder, so an HEVC clip previews on a machine whose GPU handles it and not on one that
doesn't. The app asks the window what it can decode rather than guessing, and says so
plainly when it can't.

Either way it only affects the preview. Regions can still be set from the filmstrip and the
time fields, and **the render is unaffected** — ffmpeg decodes everything on the input list.

## Levelling clips

Tick **Balance clip levels** in Project Settings → Auto-Editor, or set it in a script:

```markdown
## Auto Editor

- balance audio: yes
- audio target: -14 LUFS
```

Every clip is measured and given the trim that lands it on the target — −14 LUFS by default,
what YouTube normalises to. Switching it on levels what is already in the project and
everything added afterwards, so the preview you trim against is already levelled; a render
measures anything still unmeasured, so a hand-written script comes out level too.

**Peaks are ignored on purpose.** The level comes from where a clip sits *most of the time*,
not from its loudest moment — otherwise a single explosion in a two-hour recording decides
the level of the whole thing. Loud peaks are fine: a limiter on the finished audio keeps
them from clipping. The trim never exceeds ±24 dB and is kept separate from the manual
`volume` setting, so the two never fight.

## When footage moves

A script keeps working when its paths do not: the app loads it anyway, flags the
clips whose files are missing, and the **Relink** button on the Clips page points
one at its new home. If the rest of the missing clips are in that same folder it
offers to bring them along, so a moved drive is one action rather than one per
clip. Labels, joins, levels and trims survive the move.

The CLI is strict by design — `python script.py` still refuses to render a script
with a path that is not there, and says which line.

## Runbook

| Command | Result |
|---|---|
| `python script.py <file>.md` | Render a script |
| `python script.py templates\example.md` | Render the example template |
| `python app.py` | UI in your browser at <http://127.0.0.1:8420> |
| `python app.py --port 9000` | UI on another port |
| `python app.py --no-browser` | Server only, no browser |
| `npm install` | Install Electron (once) |
| `npm start` | Desktop app |
| `node desktop/launch.js` | Desktop app, bypassing npm |
| `ffmpeg -version` | Check ffmpeg is on your PATH |

- Stop with `Ctrl+C`, or close the app window.
- `dry run: yes` under `## Output` prints the timeline and exits without rendering.
