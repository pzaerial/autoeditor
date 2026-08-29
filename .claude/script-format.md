# Script Format Reference

A video script is a markdown file. `python script.py myvideo.md` renders it.

Only two line shapes carry meaning:

- `#` **headings** open a section.
- **list items** (`-`, `*`, `+`, `1.`, `1)`) inside a section carry values.

Everything else — paragraphs, blockquotes, tables, fenced code blocks — is
ignored, so a script doubles as the write-up for the episode.

A `# Top-level heading` that isn't a section name becomes the title.
Unknown `##` sections print a warning and are skipped. Unknown keys and unknown
timeline options are errors, reported with the line number and the valid set.

## Sections

| Heading | Aliases | Purpose |
|---|---|---|
| `## Output` | `Output Settings`, `Settings` | Where the file goes, what format it is |
| `## Defaults` | `Default` | Fallbacks for timeline items |
| `## Silence` | `Trim Silence`, `Dead Space` | Dead-space detection tuning |
| `## Assets` | `Files`, `Sources` | Named shortcuts for reused clips |
| `## Timeline` | `Sequence`, `Order` | The running order — required |

Keys are case- and punctuation-insensitive: `Fade In`, `fade_in`, `fade-in` and
`**fade in**` are the same key. Values may be wrapped in backticks or quotes.

## `## Output`

| Key | Aliases | Default | Description |
|---|---|---|---|
| `file` | `output`, `path`, `output file` | *required* | The `.mp4` to write; parent folders are created |
| `resolution` | `size` | `1920x1080` | `WxH`. Sources are letterboxed to fit |
| `fps` | `frame rate`, `framerate` | `60` | Output frame rate |
| `encoder` | `video encoder`, `codec` | `libx264` | See encoders below |
| `fade in` | | `0.5` | Fade up from black at the very start (seconds) |
| `fade out` | | `0.5` | Fade to black at the very end (seconds) |
| `dry run` | | `no` | `yes` prints the resolved timeline and stops |

Encoders with tuned quality flags: `libx264`, `libx265` (CPU); `h264_nvenc`,
`hevc_nvenc` (NVIDIA); `h264_amf` (AMD); `h264_qsv` (Intel Quick Sync). Any
other ffmpeg encoder name is passed through with `-preset fast -crf 18`.

Audio output is always AAC 192 kbps, 48 kHz stereo.

## `## Defaults`

| Key | Aliases | Default | Description |
|---|---|---|---|
| `join` | `transition` | `crossfade` | Join used when a timeline item doesn't state one |
| `crossfade` | | `0.3` | Default crossfade length (seconds) |
| `fade` | | `0.5` | Default fade-through-black length (seconds) |
| `trim silence` | `trim` | `no` | Whether clips get dead-space removal by default |

## `## Silence`

Detection is per clip and relative to that clip's own peak loudness, so it
adapts to different mic gain between sessions.

| Key | Aliases | Default | Description |
|---|---|---|---|
| `threshold` | `threshold db` | `-30` | Silence floor in dB **below the clip's peak**. More negative = less aggressive |
| `padding` | `pad` | `0.5` | Seconds kept around each loud region; gaps under `2×` this merge |
| `min silence` | `minimum silence` | `1.0` | A silence must run this long to be eligible for cutting |
| `min segment` | `minimum segment` | `0.5` | Kept regions shorter than this are dropped |

Trailing units are ignored, so `-30 dB` and `0.5s` parse fine.

## `## Assets`

Named shortcuts, so a reused clip's path is written once:

```markdown
## Assets
- intro: C:\Users\you\Videos\assets\intro.mp4
- ad 1: C:\Users\you\Videos\assets\midroll_1.mp4
```

Names are matched the same way keys are (case- and punctuation-insensitive), so
`` `Ad 1` `` in the timeline finds `ad 1`. An asset may point at a folder or
glob; it expands like any other source.

## `## Timeline`

Each item is `source` optionally followed by a separator and options:

```markdown
## Timeline
1. `intro`
2. `C:\Footage\ep12\deck-tech.mp4` -- trim silence
3. `ad 1` -- fade
4. `C:\Footage\ep12\games` -- crossfade 0.4, trim silence
```

Separator: `--`, an em dash (`—`), an en dash (`–`), or `|`, surrounded by
spaces. If the source is wrapped in backticks, the separator is found after the
closing backtick, so a path containing `--` still parses.

### Options

Comma-separated. Options describe how the clip attaches to the one **before**
it, so a timeline reads top-down like a cut list. The first item's join is
ignored.

| Option | Aliases | Meaning |
|---|---|---|
| `cut` | `hard cut` | Hard cut from the previous clip |
| `crossfade [seconds]` | `dissolve` | Blend with the previous clip |
| `fade [seconds]` | | Previous fades to black, this fades up from it |
| `trim silence` | `trim`, `remove silence`, `remove dead space` | Remove dead air from this clip |
| `keep silence` | `no trim`, `no trim silence` | Leave this clip's dead air alone |

A duration after `crossfade`/`fade` overrides the default for that join only.
`crossfade 0` and `fade 0` are treated as `cut`.

## Paths

- Windows paths work as written: `C:\Users\you\Videos\clip.mp4`.
- Spaces need no escaping or quoting: `C:\My Clips\game 1.mp4`. So do `#`, `&`, `'`, `!` and other shell metacharacters — values are read literally and passed to ffmpeg as argv, never through a shell.
- Relative paths resolve against the folder containing the markdown file — not the working directory — so a script is portable alongside its footage.
- Backticks and quotes are optional decoration.
- `: < > " | ? *` cannot appear in a Windows file or folder name. `:` is the trap — NTFS reads `title: subtitle.mp4` as the file `title` plus an alternate data stream named ` subtitle.mp4`, so a render would "succeed" and leave a 0-byte file. `_check_writable_name` rejects such an output path before any work starts, suggesting a dashed replacement. Only the output path is checked; a bad input path already fails as "file not found".
- `%USERPROFILE%`, `$HOME` and `~` are expanded.
- A **folder** expands to every video file inside it, sorted by modification time then name.
- A **glob** (`raw\ep12\game-*.mp4`) expands the same way.

Recognised video extensions: `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`, `.webm`,
`.wmv`, `.flv`.

When a folder or glob expands to several clips, **every** resulting clip takes
the item's join and options — including between themselves. So
`` `C:\raw\ep12` -- crossfade 0.3, trim silence `` crossfades the whole session
together and trims all of it.

Missing files, empty folders and globs matching nothing are errors, reported
with the line number, before any encoding starts.

## Worked example

See [../example.md](../example.md) for the classic running order:
intro, deck tech, ad, transition, games with an ad in the middle, outro.
