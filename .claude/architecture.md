# Architecture

```
script.py                — CLI entry point: python script.py myvideo.md
app.py                   — UI entry point: starts the server, opens a browser
myvideo.md               — the edit: paths, order, format (see script-format.md)

autoeditor/              the engine, and the local server the app talks to
    schema.py            — every script setting, declared once
    effects.py           — the library: every effect and transition, declared once
    timecode.py          — `1:02:03.5` ⇄ seconds, and durations for display
    tracks.py            — the timeline: Project, Track, Clip, Transition, layout
    timeline.py          — settings, and the retired flat model the migration reads
    track_script.py      — parse_project() / to_markdown() for the track format
    script_parser.py     — parse_script() → the old flat model, for migration
    script_writer.py     — the settings sections, shared by both writers
    migrate.py           — an old flat script → a track timeline
    project.py           — Project ⇄ the JSON the UI exchanges
    silence.py           — dead-space detection
    loudness.py          — programme loudness, for levelling clips
    sysmon.py            — CPU/GPU/encoder load, for the render page's graph
    probe.py             — ClipInfo; what the edit keeps of each source
    encoders.py          — encoder profiles, and what this machine can open
    compositor.py        — the filter_complex that is the whole edit
    graph.py             — the retired single-track builder, kept for comparison
    render.py            — running that one ffmpeg call, watched
    jobs.py              — render and measurement as background jobs
    library.py           — browsing files, and what can be said about each
    server.py            — HTTP: requests, responses, routing

ui/                      the app's front end
    index.html           — the four pages
    app.css              — dark theme
    js/                  — ES modules, one per concern (see ui.md)
desktop/                 — Electron shell
```

There is no `.env`, no click CLI and no hard-coded assembly order — a markdown
script (or the equivalent project built in the app) is the only input.

## How the pieces line up

The engine knows nothing about the app. `parse_project` → `probe_project` →
`build_filter_complex` → `render_project` is the whole pipeline, and `script.py`
walks it directly. The server adds two things on top: JSON in place of markdown
(`project.py`) and jobs in place of blocking calls (`jobs.py`). Nothing under
`autoeditor/` imports `server.py`.

That is what keeps the CLI honest: a project exported from the app renders
identically from the terminal, because the terminal path is not a second
implementation.

## tracks.py
The timeline. `Project` holds the settings objects and an ordered `list[Track]`;
a `Track` holds `entries`, one list of `Clip`s and the `Transition`s between them,
because that is what the script and the timeline view both show. `validate`
holds that list to alternating order.

A clip's **position is a property of the track**, not a field on the clip:
`Track.laid_out(lengths)` is the single place the layout rule lives, and
`ui/js/state.js`'s `layout()` applies the same rule so the app cannot draw an
edit the renderer would not produce. `scratchpad/layout_cross.*` checks the two
against each other over every fixture.

That one change is what makes layers, overlays, music beds and independent audio
timing expressible at all. `Join` is gone: `audio overlap` is a transition in the
library that trims the incoming picture and plays its sound underneath, and the
compiler knows it only by those two properties.

## effects.py
Every effect and transition: name, aliases, parameters, and how a transition sits
on the timeline. Declarations are plain data — the parser, the writer, the docs
and the app's inspector all read the same registry, and `/api/library` serves it
to the front end, so a new effect gets a control in the app without the UI being
told its name. The emitters live beside them, because writing a filter chain is
not something a table can express.

Adding an effect is one `EffectDef` and one entry in `_EMIT`.

## timeline.py
The settings dataclasses — `OutputSettings`, `SilenceSettings`,
`BalanceSettings`, `Defaults` — plus `VideoScript`, `TimelineClip`, `Join` and
`Region`, which now exist only so `script_parser` can read an older file and
`migrate` can convert it. Nothing new should reach for them.

`OutputSettings` is *where and what format*. `Defaults` is *how clips are joined* — to
each other and to the black either end, which is why the opening and closing fades live
there rather than in a section of their own. Those four are still the live settings model;
everything else in this module is legacy.

`GlobalEdits` is gone. Its `audio_gain_db` was a gain applied to every clip by guesswork,
which levelling now does by measurement. `balance_db` is gone too: it was a second per-clip
level holding what levelling measured while `audio_gain_db` held what a person typed, and
one outcome with two controls meant neither number told you how a clip would sound.
Levelling now writes the one `volume` effect.

## schema.py
Every setting a script can carry: its canonical spelling, its aliases, which object and
field it fills, how to read the value, and a line of help. One `Setting` per line.

The parser reads through it, the writer emits through it, and `tools/gen_reference.py`
generates the reference tables in [script-format.md](script-format.md) from it. Before,
each setting was written out three times — a key table, an apply function that knew its
type, and a line in the writer — so adding one meant three edits, and the three could
disagree without anything noticing. They did: the `join:` error still listed "cut,
crossfade or fade" a release after `audio overlap` was added.

Lookup is **global**, not per section. Sections group settings for the reader; the parser
understands a setting wherever it appears. That is what lets a file written against an
older layout open unchanged, and it removed the routing special-case that used to carry
`fade in` from `## Output` across to the joins.

`RETIRED` names settings that no longer exist, so a script carrying one is told what
replaced it instead of failing on an unknown key. `ITEM_OPTIONS` does the same job for
timeline items — it is what the "unknown timeline option" error lists, so that message
cannot fall behind the parser again.

## script_parser.py
`parse_script(path, strict=True)` → `VideoScript`, raising `ScriptError` (which carries a line number) on any problem. `strict=False` (used by the app) keeps missing files as `missing=True` placeholders and skips the output-name check, so a template from another machine still loads.

Only two line shapes are meaningful: `#` headings open sections, and list items carry values. Everything else — prose, blockquotes, fenced blocks — is skipped, so scripts double as episode notes. Unknown sections warn; unknown keys and options are hard errors listing the valid ones.

Older layouts open unchanged because section headings are advisory: `## Defaults`,
`## Global Edits` and `## Silence` are all accepted, and a setting is understood wherever
it sits. Saving from the app rewrites the file in the canonical arrangement.

Sections and settings both come from [schema.py](#schemapy); `_read` is the only place that
knows how to turn a written value into a stored one. Keys are normalised by `_norm_key`, so
`Fade In`, `fade_in` and `**fade-in**` all match.

`volume -3` and `balance +8` are matched against the raw option text, before `_norm_key`
runs — that normaliser turns a minus sign into a space, so a negative gain matched
afterwards would silently become a boost. Ranges are matched early for the same reason.

`_build_clips` resolves each timeline item: asset aliases → paths, relative paths → against the script's own folder, then `_expand_source` turns folders and globs into their video files (sorted by mtime then name). A `crossfade`/`fade` of zero length collapses to `CUT` here, so the filter graph never sees a degenerate blend.

## silence.py
`compute_keep_intervals(path, duration, settings, within=None)` → `list[(start,end)] | None` (None = keep whole clip).
Two audio-only ffmpeg passes per span: `volumedetect` (peak `max_volume`) then `silencedetect` with `noise = peak + threshold_db`. Silences are inverted into loud regions, padded, merged (gaps `< 2×padding`), and tiny segments dropped.

`within` limits the analysis to the spans the edit actually keeps, and `probe_script` passes the clip's regions. It is the difference between two audio decodes of a four-hour stream and two decodes of the twenty minutes being used — most of the silent wait before a render starts. It is also *more accurate*: the noise floor is set relative to the peak of the kept material, so a loud moment in footage already cut no longer raises the threshold for everything else. `-ss` before `-i` seeks rather than decoding up to each span, and padding is clamped to the span, so breathing room cannot reach back into material already trimmed away.

An entirely silent span returns None (keep it all) rather than nothing, since a zero-length clip would break the filter graph.

## loudness.py
`measure_loudness(path, within=None)` → `Loudness(lufs, peak_dbtp)` from one `ebur128` pass, and `balance_gain(measured, target)` → the dB trim that lands the clip on target.

**Why not R128 integrated loudness.** The standard figure gates at 10 LU below the *ungated mean*, and that mean is not robust. One loud moment drags it up until the entire quiet body of a recording falls below the gate and is discarded. Measured: adding a single 0.2 s full-scale transient to a two-minute quiet recording moved the integrated figure from −55.9 to −8.1 LUFS — a 48 dB error, which turned a clip needing a large boost into one the levelling wanted to pull *down* by 6 dB. Game audio and stream captures are full of such moments, so this is the normal case here, not an edge case.

`_typical_loudness` therefore reads the 400 ms momentary blocks (`ebur128=metadata=1` plus `ametadata` to print them) and changes two things. The relative gate hangs off the **90th percentile** of the blocks rather than their mean. What survives is combined with a **median** rather than R128's energy mean, which is itself dominated by its loudest members — six full-scale blocks among twelve hundred quiet ones still pulled the energy mean up 25 dB. The median of the gated blocks is "the level this clip sits at most of the time", which is what makes two clips sound alike.

On well-behaved material this lands within 0.1 dB of the standard integrated figure across every test file, so it is not a different scale — only a more robust estimator of the same quantity.

`measure_loudness(on_progress=, on_start=)` streams progress from the `pts_time` each block already prints, and hands over its `Popen` so a long measurement can be cancelled. `within` joins the kept spans with `atrim`/`concat` so the blocks come from exactly the material being used. Silence trimming is deliberately not applied first: the gating already discards anything well below programme level, so dead air barely moves the number and detecting it would double the wait for a small correction.

**Peaks are not consulted.** Holding a clip down because it once got loud is how one transient ends up deciding the level of a two-hour recording. The gain is a pure loudness match, clamped at ±24 dB, and `graph.py` puts an `alimiter` at `OUTPUT_CEILING` (−1 dBFS) on the finished audio whenever levelling is on, so boosted transients cannot clip. Verified: two clips at −55.8 and −3.5 LUFS with peaks at +0.4 and +4.7 dBTP rendered to −14.0 LUFS with a true peak of −0.8 dBTP.

## sysmon.py
`Sampler` polls utilisation on a background thread while a render runs, keeping one
`{t, cpu, gpu, enc}` row per second. CPU comes from `GetSystemTimes` via ctypes on Windows and
`/proc/stat` on Linux; GPU and encoder from `nvidia-smi` when it is on PATH. Anything unreadable is
`None`, which the graph draws as a missing trace rather than a flat zero — so the absence
of an AMD/Intel reading never looks like an idle GPU.

The encoder engine is reported separately because its absence was actively misleading: NVENC is
fixed-function silicon apart from the shaders, so a card encoding flat out still reports
`utilization.gpu` in the low teens.

## probe.py
- `probe_clip(path)` → `ClipInfo` (duration, resolution, fps, has_audio, codecs)
- `probe_script(script)` → `list[ClipInfo]`, resolving each clip's `regions`, running silence detection where the script asks for it, and measuring loudness where levelling is on. Results are cached per source **and spans** — the same file cut two ways is two different questions. `ClipInfo.effective_duration` reflects the post-trim length and feeds every xfade offset, fade position and progress total.

One `ClipInfo` per clip of `expand_regions(script.clips)`, which is what `render_script`
renders — anything zipping clips against `ClipInfo`s must expand too.

## encoders.py
Default quality per encoder and how each spells it, plus `encoder_available`, which finds out
by actually opening the encoder on a tiny synthetic clip and caches the answer. A hardware
encoder can be compiled into ffmpeg and still fail on a machine without that card, so this is
a question only the machine can answer.

nvenc's rate control has to be named: left to its default it honours `-cq` only loosely and
writes roughly 2.4× the size of libx264 for the same wall time.

## compositor.py
The filter_complex, and nothing that runs ffmpeg — which makes it the piece to read when an
edit comes out wrong. See [pipeline.md](pipeline.md) for the shape of the graph; the parts
worth knowing here:

- `clip_lengths` / `project_duration` → how long each clip runs and how long the edit is.
  The **picture** decides the total: an audio track outlasting the last frame is a music bed
  that gets cut off rather than extending the video.
- `is_sequential` / `rides_the_picture` → which of the two graph shapes a track needs
- `_build_video_strip` / `_build_audio_strip` → the chain, for an unbroken run
- `_build_video_canvas` / `_build_audio_track` → placement, for layers and pinned clips
- `audio_handles` → how much source each side of a join borrows, and the fades it gets
- `audio_notes` → the joins whose sound the sources could not pay for

**Why both shapes.** Placement is the general answer and the chain is an optimisation, but
not only that: laying a long HD timeline onto a canvas holds every decoder open at once, and
that was enough to starve the mix — `amix` reached the end of the clips it could see and
finished early, leaving everything after the first clip silent. The chain pulls each source
only as the timeline reaches it. Both were checked against the pre-rewrite renderer on
nineteen fixtures: video bit-identical, audio at the AAC round-trip floor.

Two differences from a naive port are worth keeping in mind, because both were found by
null-testing rather than by reading:

- A clip kept **whole** must not acquire a trim. Trimming before resampling leaves the
  resampler a different edge than trimming after it, and the difference shows up at the end
  of the edit. The strip path skips the trim; the placed path always emits it, because that
  is what it did before.
- A **dip to black** fades each side to real silence, so it takes ffmpeg's default straight
  line. Equal power (`qsin`) is right only where two signals overlap and sum.

## graph.py
The single-track builder this replaced. Kept because it is what nineteen reference renders
were made with, and it is the thing `compositor.py` is checked against; nothing calls it.

## render.py
`render_script(script, infos)` builds the graph and runs one ffmpeg command, writing no
intermediate files. Callbacks let a caller narrate what is otherwise silent: `on_stage` fires
when the graph starts and when ffmpeg is about to be spawned, `on_stderr` streams ffmpeg's own
log line by line, `on_progress` reports position, and `on_start` hands over the process so a
render can be cancelled. The CLI ignores all of them; `jobs.py` turns them into the app's
pipeline display and log.

## jobs.py
`RenderJob` and `BalanceJob`: the two pieces of work that outlive a request. Both run on a
background thread, both hold their ffmpeg `Popen` so cancel can terminate it, and both expose
a `snapshot()` the UI polls. Kept out of `server.py` because none of it is about HTTP.

`BalanceJob` weights progress by how much audio each clip contributes rather than by clip
count, so one two-hour recording among three short ones does not sit at 0% and then finish.

## library.py
The files the app offers you, and what can be said about each: browsing a folder, probing a
file for the picker, deciding whether the preview can open it, checking an output path, and
resolving where Save as… writes. Filesystem questions, not HTTP ones.

## script.py
Prints the resolved script, honours `dry run`, checks ffmpeg/ffprobe are on PATH, then probes
and renders. Reports `ScriptError` with the offending line and prints the tail of ffmpeg's
stderr if the encode fails.
