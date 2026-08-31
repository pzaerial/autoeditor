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
| `## Output` | `Output Settings`, `Settings` | Where the file goes, and what kind of file it is |
| `## Joins` | `Defaults`, `Global Edits` | How clips meet each other, and the black at either end |
| `## Auto Editor` | `Auto Edit`, `Passes`, `Silence` | The opt-in passes: levelling and silence trimming |
| `## Assets` | `Files`, `Sources` | Named shortcuts for reused clips |
| `## Video: <name>` | | A video track — one per layer, first is the bottom |
| `## Audio: <name>` | | An audio track — a music bed, a commentary pass |

At least one track section is required; everything else falls back to its
default. A setting is understood wherever it appears, so an older file with
`## Defaults` and `## Silence` still opens — saving it from the app rewrites it
in the layout above.

An older script with a single `## Timeline` section still opens too: it is read
and converted to one `Video: Main` track, and saving writes it in the track
form. Nothing about the render changes.

Keys are case- and punctuation-insensitive: `Fade In`, `fade_in`, `fade-in` and
`**fade in**` are the same key. Values may be wrapped in backticks or quotes.

## Settings

Five sections, each with one job. Headings organise the file for a reader --
the parser understands a setting wherever it appears, so a script written
against an older layout still opens, and saving it from the app rewrites it in
the arrangement below.

<!-- generated:settings -->

### `## Output`

Where the file goes, and what kind of file it is.

Also accepted as `## Output Settings`, `## Settings`.

| Key | Aliases | Default | Description |
|---|---|---|---|
| `file` | `output`, `path`, `output file` | *required* | The video to write; parent folders are created |
| `resolution` | `size` | 1920x1080 | `WxH`. Sources are letterboxed to fit |
| `fps` | `frame rate`, `framerate` | 60 | Output frame rate |
| `encoder` | `video encoder`, `codec` | libx264 | See encoders below |
| `quality` | `crf`, `cq` | per encoder | Lower is bigger and better; blank takes the encoder's default |
| `dry run` |  | no | `yes` prints the resolved timeline and stops |

### `## Joins`

How clips meet each other, and the black at either end.

Also accepted as `## Join`, `## Defaults`, `## Default`, `## Global Edits`, `## Global`, `## Globals`, `## Master`.

| Key | Aliases | Default | Description |
|---|---|---|---|
| `join` | `transition` | `crossfade` | Join used when a timeline item does not name one |
| `crossfade` |  | `0.3` | Default `crossfade` length (seconds) |
| `fade` |  | `0.5` | Default `fade` length (seconds) |
| `audio overlap` | `prelap`, `audio first` | `2` | Default `audio overlap` length (seconds) |
| `audio blend` | `audio crossfade` | *follow picture* | How long a join's sound takes to change hands; blank follows the picture |
| `audio lead` | `audio offset` | `0` | Seconds the sound changes before the picture does |
| `fade from black` | `fade in` | `0.5` | Opening fade, at the very start (seconds) |
| `fade to black` | `fade out` | `0.5` | Closing fade, at the very end (seconds) |

### `## Auto Editor`

Passes that edit the footage for you. Each is opt-in; with both off, clips render exactly as cut.

Also accepted as `## Auto Edit`, `## Passes`, `## Silence`, `## Trim Silence`, `## Dead Space`, `## Dead Space Removal`.

| Key | Aliases | Default | Description |
|---|---|---|---|
| `balance audio` | `balance`, `balance levels` | `no` | Level every clip to the same loudness |
| `audio target` | `target`, `target loudness`, `loudness` | `-14` | LUFS to level to; -14 is what YouTube normalises to |
| `trim silence` | `trim` | `no` | Remove dead air, unless a timeline item says otherwise |
| `silence threshold` | `threshold`, `threshold db` | `-30` | Silence floor in dB **below the clip's own peak** |
| `silence padding` | `padding`, `pad` | `0.5` | Seconds kept around each loud region; gaps under 2x this merge |
| `silence min length` | `min silence`, `minimum silence` | `1` | A silence must run this long to be cut |
| `silence min segment` | `min segment`, `minimum segment` | `0.5` | Kept regions shorter than this are dropped |

<!-- /generated:settings -->

Encoders with tuned quality flags: `libx264` (18), `libx265` (22); `h264_nvenc`
(23), `hevc_nvenc` (25), `av1_nvenc` (25); `h264_amf` (22), `hevc_amf` (24);
`h264_qsv` (22), `hevc_qsv` (24) — the number in brackets is that encoder's
default `quality`. Any other ffmpeg encoder name is passed through with
`-preset fast -crf 18`.

A hardware encoder renders roughly **twice as fast**: on a three-clip 1080p60
edit, `libx264` took 12.4s and `h264_nvenc` 5.5s. It does not free the CPU,
though — decoding every input and running the transitions is CPU work whatever
encodes the result, and that alone accounted for 3.9s of those 5.5s. Expect a
hardware render to be about twice as quick with the CPU still busy.

Hardware encoders can be compiled into ffmpeg and still fail to open on a
machine without that card. The app checks and greys out the ones it cannot use;
from the CLI a wrong choice fails at the start of the render.

Audio output is always AAC 192 kbps, 48 kHz stereo. The container follows the
output file's extension: `.mp4`, `.mov` and `.mkv` all take these codecs, and
`+faststart` is applied to the mp4-family ones that have an index to move.

### Retired options

`## Timeline` was one flat list of clips, each carrying the join that attached it
to the one before. That is why there could only ever be one strip of video, and
why sound could only be moved off its picture through two options bolted onto the
following clip. A script written that way still opens: it is read, converted to a
single `Video: Main` track, and renders identically. Saving writes the track form.

`balance [dB]` was a second per-item level, holding what auto-balance measured
while `volume` held what you typed. One outcome with two controls meant neither
number told you how a clip would sound, so there is one now: auto-balance writes
`volume`. A script still carrying `balance` opens and renders the same -- the two
are added -- and saving it writes the single total.

### Retired settings

`audio adjust` (a gain applied to every clip) was removed: levelling in
`## Auto Editor` does the same job by measurement rather than by guess. A script
still carrying it is warned and otherwise unaffected.

`fade in` and `fade out` were once in `## Output` and then in `## Global Edits`.
They are join settings and now live in `## Defaults`; both older spellings are
still read, so no script needs rewriting.

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

## Tracks

A track is a section; its entries are a numbered list. An entry is either a
**clip** or a **transition**, and they alternate — a track cannot open or close
with a transition, or carry two in a row.

```markdown
## Video: Main
1. `intro`
   - volume -9.6 dB
2. crossfade 0.3
3. `C:\Users\you\Videos\stream.mp4` @ 1:05:29.724-1:10:09.021
   - trim silence
   - volume +12.6 dB
4. audio overlap 69
5. `outro`

## Audio: Music
- gain: -18 dB
1. `C:\Users\you\Music\bed.mp3` at 0:05
   - fade in 2
   - fade out 3
```

Transitions are entries in their own right rather than options on the clip that
follows, because that is what they are on a timeline: something between two
clips that you can point at and give settings of its own.

**A bullet at the left margin is a track setting. An indented one belongs to the
entry above it.** That is the whole indentation rule.

### Where a clip sits

Clips are sequential: each starts where the last ended, minus the overlap of the
transition joining them. `at <time>` pins a clip to a moment of its own instead,
which is what an overlay, a title or a music bed needs — and what an ordinary
cut list never has to write.

Video tracks stack in the order they appear: the **first is the bottom layer**,
and a later one covers it. Audio tracks all mix together, so their order is
presentation only.

### Clip entries

| Part | Meaning |
|---|---|
| `` `path` `` or `` `asset` `` | The source. A folder or glob expands to its video files, oldest first |
| `@ 2:10-5:30` | Keep only this range of the source |
| `at 0:05` | Pin the clip to this point on the timeline |

### Track settings

| Key | Meaning |
|---|---|
| `gain: -18 dB` | Level for everything on the track |
| `muted: yes` | Leave the track out of the mix |
| `hidden: yes` | Leave a video track out of the picture |

### Effects

One per bullet under a clip, applied in the order they are listed.

| Effect | Aliases | Takes | Does |
|---|---|---|---|
| `volume` | `gain`, `level` | dB | The clip's level — what auto-balance writes, and what you adjust |
| `fade in` | `fadein` | seconds | Up from black and silence at the clip's own start |
| `fade out` | `fadeout` | seconds | Down to black and silence at the clip's own end |
| `trim silence` | `trim`, `remove silence` | — | Drop this clip's dead air |
| `keep silence` | `no trim` | — | Leave it alone, even with trimming on for the whole edit |

### Transitions

| Transition | Aliases | Picture | Sound |
|---|---|---|---|
| `cut` | `hard cut` | Straight from one to the next | Follows |
| `crossfade d` | `dissolve`, `mix` | The two overlap and blend for `d` | Equal-power crossfade over `d` |
| `dip to black d` | `fade`, `dip` | First falls to black, second rises out of it | Each side to silence, straight line |
| `audio overlap d` | `prelap`, `j-cut` | Cut; the incoming clip gives up `d` of its head | Incoming heard *under* the outgoing for `d` |

A `crossfade` shortens the edit by its duration; a `dip to black` does not.
`audio overlap` does not either — the incoming clip pays for its early sound
with the picture it gave up, so its own sound and picture stay locked.

Two bullets under a transition give its sound a timeline of its own:

```markdown
4. crossfade 0.5
   - audio 8
   - lead 4
```

`audio` is how long the sound takes to change hands; `lead` is how many seconds
before the picture it does so. Positive is a **J-cut** — the next clip is heard
before it is seen. Negative is an **L-cut** — the last clip is still heard over
the new picture. Both are paid for out of the clips' own material either side of
the cut, so the timeline never shifts; where a source has none to give, the ask
shrinks and the render says so.

### Crossfade shape

An audio crossfade is equal power (`qsin`), not linear. Two unrelated signals
ramped linearly sum to about −3 dB where they meet, so a long linear crossfade
audibly sags in the middle; a quarter-sine pair holds the level flat. Inaudible
across a 0.3 s join, obvious across ten — which is the length an `audio overlap`
tends to be.

A `dip to black` is the exception: each side falls to real silence with nothing
to sum against, so it takes a straight line, which is also what an editor
expects of a fade.

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

```markdown
# Daily Dub 47

## Output
- file: `C:\Users\you\Videos\out\Daily Dub 47.mp4`
- resolution: 1920x1080
- fps: 60
- encoder: h264_nvenc

## Auto Editor
- balance audio: yes
- audio target: -14 LUFS

## Video: Main
1. `intro`
2. crossfade 0.3
3. `C:\Users\you\Videos\capture.mp4` @ 1:05:29.724-1:10:09.021
   - trim silence
4. audio overlap 69
5. `outro`

## Video: Lower third
1. `C:\Users\you\Assets\name-card.mov` at 0:08

## Audio: Music
- gain: -20 dB
1. `C:\Users\you\Music\bed.mp3` at 0:00
   - fade in 2
   - fade out 4
```

The outro is heard 69 seconds before it is seen, under the tail of the capture,
and gives up 69 seconds of its own picture to pay for it. The name card sits on
its own layer over the intro; the bed runs underneath everything and is cut off
where the picture ends.
