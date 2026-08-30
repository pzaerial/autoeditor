# Architecture

```
script.py                — CLI entry point: python script.py myvideo.md
app.py                   — UI entry point: starts the server, opens a browser
myvideo.md               — the edit: paths, order, format (see script-format.md)
autoeditor/
    timeline.py          — data model: VideoScript, TimelineClip, Join, settings
    timecode.py          — `1:02:03.5` ⇄ seconds
    script_parser.py     — parse_script() → VideoScript from a markdown file
    script_writer.py     — to_markdown() → VideoScript back to a script file
    project.py           — VideoScript ⇄ the JSON the UI exchanges
    server.py            — stdlib HTTP backend for the UI (see ui.md)
    silence.py           — compute_keep_intervals() → loud regions for dead-space removal
    loudness.py          — measure_loudness() → EBU R128 LUFS + true peak, for levelling clips
    sysmon.py            — CPU/GPU sampling for the render page's load graph
    ffmpeg_ops.py        — all ffmpeg work: probe, normalize, xfade, concat, fades
ui/                      — the app's HTML/CSS/JS
desktop/                 — Electron shell
```

There is no `.env`, no click CLI and no hard-coded assembly order — a markdown script (or
the equivalent project built in the app) is the only input.

## timeline.py
Pure data, no logic. `VideoScript` holds `OutputSettings`, `SilenceSettings`, `BalanceSettings`, `Defaults` and an ordered `list[TimelineClip]`.

`OutputSettings` is *where and what format*. `Defaults` is *how clips are joined* — to each
other and to the black either end, which is why the opening and closing fades live there
rather than in a section of their own.

`GlobalEdits` is gone. Its `audio_gain_db` was a gain applied to every clip by guesswork,
which levelling now does by measurement; keeping both would have meant two ways to set the
same thing, applied in sequence. Its fades were join settings all along.

Each `TimelineClip` carries the `Join` describing how it attaches to the clip **before** it (`CUT`, `CROSSFADE`, `FADE`) plus that join's duration, so the whole edit is a flat list. `regions` optionally narrows a clip to `[(start, end)]` ranges of its source; `audio_gain_db` is that clip's own level trim; `audio_overlap` and `audio_lead` describe how the join's *sound* is handled when it should not simply follow the picture (`audio_follows_picture()` is the test the renderer branches on); `missing` flags a file that was absent when the script was loaded non-strictly. `VideoScript.describe()` renders the summary printed before a render.

## script_parser.py
`parse_script(path, strict=True)` → `VideoScript`, raising `ScriptError` (which carries a line number) on any problem. `strict=False` (used by the app) keeps missing files as `missing=True` placeholders and skips the output-name check, so a template from another machine still loads.

Only two line shapes are meaningful: `#` headings open sections, and list items carry values. Everything else — prose, blockquotes, fenced blocks — is skipped, so scripts double as episode notes. Unknown sections warn; unknown keys and options are hard errors listing the valid ones.

`fade in` and `fade out` are join settings in `## Defaults`, but were once written under
`## Output` and later under `## Global Edits`. `_JOIN_FROM_OUTPUT` routes the first spelling
across and `_SECTION_ALIASES` maps the second onto `## Defaults`, so no existing script
needs rewriting. `_RETIRED_KEYS` names settings that no longer exist -- `audio adjust` --
so a script still carrying one is told what replaced it rather than failing on an unknown
key.

Section aliases live in `_SECTION_ALIASES`; key aliases in `_OUTPUT_KEYS` / `_GLOBAL_KEYS` /
`_SILENCE_KEYS` / `_DEFAULT_KEYS`. Keys are normalised by `_norm_key` so `Fade In`, `fade_in` and `**fade-in**` all match.

`volume -3` is matched against the raw option text, before `_norm_key` runs — that
normaliser turns a minus sign into a space, so a negative gain matched afterwards would
silently become a boost. Ranges are matched early for the same reason.

`_build_clips` resolves each timeline item: asset aliases → paths, relative paths → against the script's own folder, then `_expand_source` turns folders and globs into their video files (sorted by mtime then name). A `crossfade`/`fade` of zero length collapses to `CUT` here, so the filter graph never sees a degenerate blend.

## silence.py
`compute_keep_intervals(path, duration, settings, within=None)` → `list[(start,end)] | None` (None = keep whole clip).
Two audio-only ffmpeg passes per span: `volumedetect` (peak `max_volume`) then `silencedetect` with `noise = peak + threshold_db`. Silences are inverted into loud regions, padded, merged (gaps `< 2×padding`), and tiny segments dropped. Called from `probe_script` for clips marked `trim silence`.

`within` limits the analysis to the spans the edit actually keeps, and `probe_script` passes the clip's regions. It is the difference between two audio decodes of a four-hour stream and two decodes of the twenty minutes being used — most of the silent wait before a render starts. It is also *more accurate*: the noise floor is set relative to the peak of the kept material, so a loud moment in footage already cut no longer raises the threshold for everything else. `-ss` before `-i` seeks rather than decoding up to each span, and padding is clamped to the span, so breathing room cannot reach back into material already trimmed away.

The cache in `probe_script` is keyed by source *and* spans: the same file cut two ways is two different questions.

An entirely silent span returns None (keep it all) rather than nothing, since a zero-length clip would break the filter graph.

## ffmpeg_ops.py
Single-pass architecture — all source clips are inputs to one ffmpeg call. No intermediate files.

- `probe_clip(path)` → `ClipInfo` (duration, resolution, fps, has_audio)
- `probe_script(script)` → `list[ClipInfo]`, resolving each clip's `regions` and running silence detection where the script asks for it; when both apply they are intersected. Results are cached per source path, so a clip reused across the timeline is analysed once. `ClipInfo.effective_duration` reflects the post-trim length and feeds every xfade offset, fade position and progress total.
- `_split_into_groups(clips)` → runs of clips joined by `CROSSFADE`; `CUT` and `FADE` joins end a group
- `_group_duration(indices, infos, clips)` → a group's output length after xfade overlap (per-join durations, not one global value)
- `_build_filter_complex(infos, clips, groups, script)` → the complete filter_complex string:
  - Per-clip: when `keep_intervals` is set, `split → trim/atrim each interval → concat` to drop dead space in-graph; then `scale/pad/fps/format/setsar` for video, `aresample/aformat` for audio, `anullsrc` for clips with no audio track. `GlobalEdits.audio_gain_db + clip.audio_gain_db` becomes one `volume=NdB` in that audio chain, omitted when the sum is zero
  - Per-group: `xfade`+`acrossfade` chain across the group's clips
  - At `FADE` boundaries: `fade=out`/`afade=out` on the group before, `fade=in`/`afade=in` on the group after
  - Final: `concat` across all groups, then the output's own `fade=in/out`+`afade=in/out`
- `render_script(script, infos)` → builds the graph and runs one ffmpeg command with live progress
- `audio_notes(script, infos)` → the joins whose requested audio overlap the sources could not pay for

### Audio placement

`_video_offsets` is the one place that says where each clip's picture starts;
the xfade offsets and the audio layout both read it, so they cannot drift apart.

Sound normally tiles exactly like picture and rides the same concat/acrossfade
chain. When a join sets `audio_overlap` or `audio_lead`, `_uses_audio_offsets`
switches the *audio* half of the graph to `_place_audio`: each clip's audio is
trimmed, faded at its edges, `adelay`ed to an absolute position and `amix`ed
(`normalize=0`), while the picture concatenates as before.

`_audio_layout` does the arithmetic. For each join it works out how much source
the request needs either side of the cut:

```
head = lead + (blend - join_duration) / 2    extra source before the in point
tail = (blend - join_duration) / 2 - lead    extra source after the out point
```

Both are clamped to what the source actually has outside the clip's own in and
out points (and to 45% of a clip, so nothing is asked to give up its middle).
Because head and tail are exactly complementary across a join, the segments
still tile: the timeline never shifts, the total is still the picture's, no gap
opens, and only the requested join loses lock with its picture. A shortfall is
recorded per join and surfaced by `audio_notes`.

Both paths were checked against each other on cut, crossfade, fade, region-split
and gain-bearing edits: they agree to about −84 dBFS, i.e. AAC round-trip noise.
The chain stays the default anyway — it is what every existing project renders
through, and it pulls each source only as the timeline reaches it, where the mix
holds every segment open at once.

`_pin` forces each clip's audio to exactly the length its picture occupies. A
decoder's idea of a clip's length is not the timeline's — AAC pads its final
frame by up to ~20 ms, and a capture can hand back an audio track shorter than
its video — and `concat` believes whatever it is given, so without this each
clip's slack pushed every later clip out of step with its own picture, a drift
that grew with the clip count instead of cancelling.

Callbacks let a caller narrate the parts that are otherwise silent: `probe_script(on_step=)`
fires per clip and again before each silence pass, `render_script(on_stage=)` fires when
the graph starts and when ffmpeg is about to be spawned, and `render_script(on_stderr=)`
streams ffmpeg's own log line by line. The CLI ignores all three; the app's `RenderJob`
turns them into its pipeline display and log.

## loudness.py
`measure_loudness(path, within=None)` → `Loudness(lufs, peak_dbtp)` from one `ebur128` pass, and `balance_gain(measured, target)` → the dB trim that lands the clip on target.

**Why not R128 integrated loudness.** The standard figure gates at 10 LU below the *ungated mean*, and that mean is not robust. One loud moment drags it up until the entire quiet body of a recording falls below the gate and is discarded. Measured: adding a single 0.2 s full-scale transient to a two-minute quiet recording moved the integrated figure from −55.9 to −8.1 LUFS — a 48 dB error, which turned a clip needing a large boost into one the levelling wanted to pull *down* by 6 dB. Game audio and stream captures are full of such moments, so this is the normal case here, not an edge case.

`_typical_loudness` therefore reads the 400 ms momentary blocks (`ebur128=metadata=1` plus `ametadata` to print them) and changes two things. The relative gate hangs off the **90th percentile** of the blocks rather than their mean. What survives is combined with a **median** rather than R128's energy mean, which is itself dominated by its loudest members — six full-scale blocks among twelve hundred quiet ones still pulled the energy mean up 25 dB. The median of the gated blocks is "the level this clip sits at most of the time", which is what makes two clips sound alike.

On well-behaved material this lands within 0.1 dB of the standard integrated figure across every test file, so it is not a different scale — only a more robust estimator of the same quantity.

`measure_loudness(on_progress=, on_start=)` streams progress from the `pts_time` each block already prints, and hands over its `Popen` so a long measurement can be cancelled. `within` joins the kept spans with `atrim`/`concat` so the blocks come from exactly the material being used. Silence trimming is deliberately not applied first: the gating already discards anything well below programme level, so dead air barely moves the number and detecting it would double the wait for a small correction.

**Peaks are not consulted.** Holding a clip down because it once got loud is how one transient ends up deciding the level of a two-hour recording. The gain is a pure loudness match, clamped at ±24 dB, and `_build_filter_complex` puts an `alimiter` at `OUTPUT_CEILING` (−1 dBFS) on the finished audio whenever levelling is on, so boosted transients cannot clip. Verified: two clips at −55.8 and −3.5 LUFS with peaks at +0.4 and +4.7 dBTP rendered to −14.0 LUFS with a true peak of −0.8 dBTP.

The result lands in `TimelineClip.balance_db`, deliberately **not** in `audio_gain_db`: keeping the measured level apart from the manual trim means neither overwrites the other and neither is applied twice. `None` means "not measured yet", which is what lets both ends fill it in without racing — the app measures on tick and on import so the preview is levelled, `probe_script` measures whatever is still `None` so a hand-written script renders level too, and a value already in the script is trusted rather than re-measured. `_build_filter_complex` sums `globals.audio_gain_db + clip.audio_gain_db + info.balance_db` into the one `volume` filter.

## sysmon.py
`Sampler` polls utilisation on a background thread while a render runs, keeping one
`{t, cpu, gpu}` row per second. CPU comes from `GetSystemTimes` via ctypes on Windows and
`/proc/stat` on Linux; GPU from `nvidia-smi` when it is on PATH. Anything unreadable is
`None`, which the graph draws as a missing trace rather than a flat zero — so the absence
of an AMD/Intel reading never looks like an idle GPU.

## script.py
Prints the resolved script, honours `dry run`, checks ffmpeg/ffprobe are on PATH, then probes and renders. Reports `ScriptError` with the offending line and prints the tail of ffmpeg's stderr if the encode fails.
