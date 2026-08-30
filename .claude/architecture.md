# Architecture

```
script.py                — CLI entry point: python script.py myvideo.md
app.py                   — UI entry point: starts the server, opens a browser
myvideo.md               — the edit: paths, order, format (see script-format.md)

autoeditor/              the engine, and the local server the app talks to
    schema.py            — every script setting, declared once
    timecode.py          — `1:02:03.5` ⇄ seconds, and durations for display
    timeline.py          — data model: VideoScript, TimelineClip, Join, settings
    script_parser.py     — parse_script() → VideoScript from a markdown file
    script_writer.py     — to_markdown() → VideoScript back to a script file
    project.py           — VideoScript ⇄ the JSON the UI exchanges
    silence.py           — dead-space detection
    loudness.py          — programme loudness, for levelling clips
    sysmon.py            — CPU/GPU/encoder load, for the render page's graph
    probe.py             — ClipInfo; what the edit keeps of each source
    encoders.py          — encoder profiles, and what this machine can open
    graph.py             — the filter_complex that is the whole edit
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

The engine knows nothing about the app. `parse_script` → `probe_script` →
`build_filter_complex` → `render_script` is the whole pipeline, and `script.py`
walks it directly. The server adds two things on top: JSON in place of markdown
(`project.py`) and jobs in place of blocking calls (`jobs.py`). Nothing under
`autoeditor/` imports `server.py`.

That is what keeps the CLI honest: a project exported from the app renders
identically from the terminal, because the terminal path is not a second
implementation.

## timeline.py
Pure data, no logic. `VideoScript` holds `OutputSettings`, `SilenceSettings`, `BalanceSettings`, `Defaults` and an ordered `list[TimelineClip]`.

`OutputSettings` is *where and what format*. `Defaults` is *how clips are joined* — to each
other and to the black either end, which is why the opening and closing fades live there
rather than in a section of their own.

Each `TimelineClip` carries the `Join` describing how it attaches to the clip **before** it (`CUT`, `CROSSFADE`, `FADE`) plus that join's duration, so the whole edit is a flat list. `regions` optionally narrows a clip to `[(start, end)]` ranges of its source; `audio_gain_db` is that clip's own level trim; `balance_db` is what levelling worked out, kept apart so neither overwrites the other; `audio_blend` and `audio_lead` describe how the join's *sound* is handled when it should not simply follow the picture (`audio_follows_picture()` is the test the renderer branches on); `missing` flags a file that was absent when the script was loaded non-strictly. `VideoScript.describe()` renders the summary printed before a render.

`expand_regions` lives here too: splitting a clip whose regions blend into one clip per region is a transformation of the model, and needs nothing from ffmpeg. Regions joined by plain cuts stay together and are dropped in-graph by `keep_intervals`, which needs only one ffmpeg input. A region that crossfades or fades into the one before it cannot be expressed that way, so the clip becomes several clips and the ordinary join machinery takes over.

`GlobalEdits` is gone. Its `audio_gain_db` was a gain applied to every clip by guesswork,
which levelling now does by measurement; keeping both would have meant two ways to set the
same thing, applied in sequence. Its fades were join settings all along.

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

## graph.py
The filter_complex, and nothing that runs ffmpeg — which makes it the piece to read when an
edit comes out wrong.

- `split_into_groups(clips)` → runs of clips joined by `CROSSFADE`; `CUT` and `FADE` joins end a group
- `group_duration` / `total_duration` → a group's output length after xfade overlap (per-join durations, not one global value)
- `video_offsets` → where each clip's picture starts; the xfade offsets and the audio layout both read it, so they cannot drift apart
- `build_filter_complex(infos, clips, groups, script)` → the complete filter_complex:
  - Per-clip: when `keep_intervals` is set, `split → trim/atrim each interval → concat` to drop dead space in-graph; then `scale/pad/fps/format/setsar` for video, `aresample/aformat` for audio, `anullsrc` for clips with no audio track. The clip's own trim and its levelling become one `volume=NdB`
  - Per-group: `xfade`+`acrossfade` chain across the group's clips
  - At `FADE` boundaries: `fade=out`/`afade=out` on the group before, `fade=in`/`afade=in` on the group after
  - Final: `concat` across all groups, the limiter when levelling is on, then the output's own `fade=in/out`+`afade=in/out`
- `audio_notes(script, infos)` → the joins whose requested audio overlap the sources could not pay for

### Audio placement

Sound normally tiles exactly like picture and rides the same concat/acrossfade chain. When a
join sets `audio_blend`, `audio_lead` or an `audio overlap` join, `uses_audio_offsets` switches the *audio* half of
the graph to `_place_audio`: each clip's audio is trimmed, faded at its edges, `adelay`ed to
an absolute position and `amix`ed (`normalize=0`), while the picture concatenates as before.

`audio_layout` does the arithmetic. For each join it works out how much source the request
needs either side of the cut:

```
head = overlap + lead + (blend - join_duration) / 2   source before the in point
tail = (blend - join_duration) / 2 - lead             source after the out point
```

`overlap` is non-zero only for an `audio overlap` join, where it is the whole story:
`probe.py` drops that many seconds off the head of the clip's *picture*, and the head
above takes exactly that back for its *sound*. The two stay locked to each other, and
no handles are needed, because the sound is paid for by the picture that was given up.

Both fades then span `min(blend, overlap)` on an equal-power curve (`CROSSFADE_CURVE`),
so a ten-second overlap is a ten-second crossfade. A linear pair would sag about 3 dB
where they meet; inaudible over 0.3 s, obvious over ten.

Both are clamped to what the source actually has outside the clip's own in and out points
(and to 45% of a clip, so nothing is asked to give up its middle). Because head and tail are
exactly complementary across a join, the segments still tile: the timeline never shifts, the
total is still the picture's, no gap opens, and only the requested join loses lock with its
picture. A shortfall is recorded per join and surfaced by `audio_notes`.

Both paths were checked against each other on cut, crossfade, fade, region-split and
gain-bearing edits: they agree to about −84 dBFS, i.e. AAC round-trip noise. The chain stays
the default anyway — it is what every existing project renders through, and it pulls each
source only as the timeline reaches it, where the mix holds every segment open at once.

`_pin` forces each clip's audio to exactly the length its picture occupies. A decoder's idea
of a clip's length is not the timeline's — AAC pads its final frame by up to ~20 ms, and a
capture can hand back an audio track shorter than its video — and `concat` believes whatever
it is given, so without this each clip's slack pushed every later clip out of step with its
own picture, a drift that grew with the clip count instead of cancelling.

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
