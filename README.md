# FullControl MTG – Auto Editor
Markdown driven video autoeditor written in Python.

```
python script.py myvideo.md
```

Control your video format by writing a markdown file.

## Quick start

Copy a script out of [templates/](templates/), point the paths at your own files, and run it:

```
python script.py templates\example.md
```

Set `dry run: yes` in the `## Output` section to print the resolved timeline without rendering — a fast way to check every path and join before committing to an encode.

---

## The script format

A script is ordinary markdown. `##` headings open sections, and the list items inside them carry the settings. Anything that isn't a heading or a list item — paragraphs, blockquotes, fenced code — is ignored, so a script doubles as your notes for the episode.

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

| Section | Purpose |
|---|---|
| `# Title` | The episode name. Cosmetic — printed when rendering. |
| `## Output` | Where the finished file goes and what format it is. |
| `## Defaults` | Fallbacks for timeline items that don't state their own. |
| `## Silence` | Tuning for dead-space removal. |
| `## Assets` | Named shortcuts for clips you reuse (`intro`, `ad 1`, …). |
| `## Timeline` | The running order. This is the edit. |

### Output

| Key | Default | Description |
|---|---|---|
| `file` | *required* | The `.mp4` to write. Parent folders are created for you. |
| `resolution` | `1920x1080` | Output size as `WxH`. Sources are letterboxed to fit. |
| `fps` | `60` | Output frame rate. |
| `encoder` | `libx264` | `libx264` (CPU), `h264_nvenc` (NVIDIA), `h264_amf` (AMD), `h264_qsv` (Intel). |
| `fade in` | `0.5` | Fade up from black at the very start, in seconds. |
| `fade out` | `0.5` | Fade to black at the very end, in seconds. |
| `dry run` | `no` | `yes` prints the resolved timeline and stops. |

### Defaults

| Key | Default | Description |
|---|---|---|
| `join` | `crossfade` | How clips attach when the timeline item doesn't say. |
| `crossfade` | `0.3` | Default crossfade length in seconds. |
| `fade` | `0.5` | Default fade-through-black length in seconds. |
| `trim silence` | `no` | Whether clips get dead-space removal by default. |

### Silence

Dead-space removal is per clip and relative to that clip's own peak loudness, so it adapts to different mic gain between sessions.

| Key | Default | Description |
|---|---|---|
| `threshold` | `-30 dB` | Silence floor **below the clip's peak**. More negative = less aggressive. |
| `padding` | `0.5` | Seconds kept around each loud region. Gaps shorter than `2×` this merge. |
| `min silence` | `1.0` | A silence must run this long before it's eligible to be cut. |
| `min segment` | `0.5` | Kept regions shorter than this are dropped. |

### Timeline

Each item is a file, folder, glob or asset name, optionally followed by `--` and a comma-separated list of options:

```markdown
1. `intro`
2. `C:\Footage\ep12\game-1.mp4` -- crossfade 0.5, trim silence
3. `C:\Assets\ad.mp4` -- fade
```

| Option | Meaning |
|---|---|
| `cut` | Hard cut from the previous clip. |
| `crossfade [seconds]` | Blend with the previous clip. Uses the default length if unstated. |
| `fade [seconds]` | Previous clip fades to black, this one fades up from it. |
| `trim silence` | Remove dead air from this clip. |
| `keep silence` | Leave this clip's dead air alone (overrides the default). |

Options describe how a clip attaches to the one **before** it, so they're read top-down like a cut list. The first item's join is ignored.

The separator can be `--`, an em dash, or `|`. Numbered and bulleted lists both work.

### Paths

- Windows paths work as-is: `C:\Users\you\Videos\clip.mp4`.
- **Spaces need no escaping or quoting** — `C:\My Clips\game 1.mp4` is fine, as are `#`, `&`, `'` and `!`.
- Relative paths resolve against the folder holding the markdown file.
- Backticks or quotes around a path are optional — use them if the path contains `--`.
- `: < > " | ? *` are **not** valid in a Windows file or folder name. A `:` is the dangerous one: Windows silently writes an empty file plus a hidden data stream instead of failing, so the output path is checked up front and rejected with a suggested replacement.
- Environment variables (`%USERPROFILE%`, `$HOME`) and `~` are expanded.
- A **folder** expands to every video inside it, sorted by modification time then name.
- A **glob** (`raw\ep12\game-*.mp4`) expands the same way.

When a folder or glob expands to several clips, they all take the item's join and options — so `` `C:\raw\ep12` -- crossfade 0.3, trim silence `` crossfades the whole recording session together.

Supported input formats: `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`, `.webm`, `.wmv`, `.flv`.

---

## How it renders

Everything happens in **one ffmpeg pass** with no intermediate files. Each clip is normalised (scaled and letterboxed to the target resolution, resampled to the target fps, audio to 48 kHz stereo), then:

- Runs of clips joined by `crossfade` are blended together with an `xfade`/`acrossfade` chain.
- `cut` and `fade` joins break those runs apart and hard-cut between them; a `fade` join dips both sides to black first.
- The assembled video gets its final fade-in and fade-out.

Clips marked `trim silence` are analysed first with two cheap audio-only passes (`volumedetect` for the peak, then `silencedetect` with a floor relative to it). The loud regions are padded, merged and trimmed in-graph, so no re-encode happens twice.

Progress is reported live while ffmpeg runs.

---

## Notes

- The same clip can appear in the timeline as many times as you like; silence analysis is cached per file.
- A clip with no audio track gets a silent one synthesised so the concat stays in sync.
- `crossfade 0` is treated as a `cut`.
