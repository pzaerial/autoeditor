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
| `## Timeline` | `Sequence`, `Order` | The running order — required |

Only `## Timeline` is required; everything else falls back to its default. A
setting is understood wherever it appears, so an older file with `## Defaults`
and `## Silence` still opens — saving it from the app rewrites it in the layout
above.

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
| `audio overlap [seconds]` | `audio first`, `prelap`, `j-cut` | This clip is *heard* that many seconds before it is *seen* |
| `trim silence` | `trim`, `remove silence`, `remove dead space` | Remove dead air from this clip |
| `keep silence` | `no trim`, `no trim silence` | Leave this clip's dead air alone |
| `volume [dB]` | `gain`, `audio` | This clip's level. Auto-balance writes it; edit it and your value stands |
| `audio blend [seconds]` | `audio crossfade` | Length of this join's audio transition; `auto` follows the picture |
| `audio lead [seconds]` | `audio offset` | Seconds the audio transition happens before the picture cut |
| `2:10-5:30` | `2:10 to 5:30` | Keep only this range of the source |

A duration after `crossfade`/`fade` overrides the default for that join only.
`crossfade 0` and `fade 0` are treated as `cut`.

### `audio overlap` — heard before it is seen

```markdown
4. `C:\Assets\outro.mp4` -- audio overlap 10
```

The picture cuts as usual, but this clip's sound starts ten seconds earlier, under
the picture of the clip before it. To pay for that, **the first ten seconds of this
clip's picture are dropped**: at the cut you see it already ten seconds in, exactly
where its own sound has reached. Sound and picture stay locked to each other; only
their arrival is staggered.

That is what makes it different from `audio lead`, which slides a join's sound off
its picture and needs spare source either side of the cut to do it. An `audio
overlap` join needs no handles at all — it takes the sound from the picture it gave
up. The cost is length: a ten-second overlap makes the finished video ten seconds
shorter, because that much picture is gone.

It is the natural join for an intro or outro with a music bed: the music starts under
the last shot, then the picture cuts to it already playing.

Both sides ramp across the **whole** overlap — the incoming rising as the outgoing
falls — so a ten-second overlap is a ten-second crossfade, not a quick handover with
a long tail. `audio blend` shortens the ramp if you want the change to happen faster
than the overlap itself.

### Crossfade shape

Audio crossfades use an **equal-power** curve (a quarter-sine pair). Two unrelated
signals ramped linearly sum to about −3 dB where they meet, so a linear crossfade
sags in the middle; equal power holds the level flat because sin² + cos² = 1. The
difference is inaudible across a 0.3 s join and obvious across ten seconds, which is
the length an `audio overlap` join tends to be.

### Sound across a join

By default a join's sound changes exactly where its picture does. `audio overlap`
and `audio lead` separate the two, which is how you carry a music bed across a
hard cut, or let the outgoing clip's sound run under the incoming picture.

- **`audio blend N`** — the audio transition is a crossfade `N` seconds long,
  however the picture is joined. `-- cut, audio overlap 3` hard-cuts the picture
  while the sound blends over three seconds.
- **`audio lead N`** — the centre of that transition sits `N` seconds *before*
  the picture cut. Positive is a J-cut, the incoming clip heard before it is
  seen; negative is an L-cut, the outgoing clip still heard over the new
  picture.

```markdown
4. `C:\Assets\outro.mp4` -- cut, audio blend 3, audio lead 1
```

The overlap is paid for out of the clips' own source either side of the cut: the
outgoing clip plays on past its out point, the incoming one starts before its in
point. So the timeline never shifts, the total length is still the picture's, no
silent gap appears, and only the join you asked about loses lock with its
picture.

That also means **a clip used to its very last frame has nothing to give**. Trim
its picture back — with a range, or on the app's Edit page — and the material
you trimmed becomes the handle the overlap plays from. Where the sources cannot
cover the request it is reduced to what they can, and both the CLI and the app
print how much was actually available.

`volume` takes a signed number and an optional `dB`: `volume +4`, `gain -2.5 dB`,
`audio 0`. It stacks with `balance`, so a clip levelled to `+8` and trimmed by
`-2` renders at `+6`. It is matched against the raw option text,
before options are normalised — that normaliser turns a minus into a space, which
would otherwise read `volume -3` as a boost.

### Ranges

A range keeps only part of a source, which is how you pull sections out of a long stream:

```markdown
5. `C:\Footage\stream.mp4` -- 2:10-5:30, 41:00-52:20, trim silence
```

- Timecodes are `SS`, `MM:SS` or `HH:MM:SS`, with optional decimals (`0:05.25`).
- Repeat the option for several ranges; they are sorted and kept in source order.
- A clip with no range plays in full.
- Ranges are clamped to the clip's real duration, so an end past the end is harmless.
- The end must come after the start, or the script is rejected with its line number.
- Ranges combine with `trim silence`: silence is removed from **within** the kept ranges.
- A join written *after* a range applies to the range that follows it, so sections of one
  clip can blend: `-- fade, 2:10-5:30, crossfade 0.5, 41:00-52:20`. The first join (before
  any range) is still the clip's join to the previous clip.
- Ranges must not overlap; overlapping ones are rejected with the line number.

Ranges are matched before a timeline option is normalised, so their `-` survives; that is
why `2:10-5:30` is unambiguous next to the ` -- ` separator.

These are what the app's Edit page writes when you mark regions on a clip, and they parse
back to the same edit — a render from the app and a `python script.py` render of its
export produce identical output.

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

See [../templates/example.md](../templates/example.md) for the classic running order:
intro, deck tech, ad, transition, games with an ad in the middle, outro.
