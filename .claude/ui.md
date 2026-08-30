# The App

Two front ends over one engine. `script.py` renders a markdown file headlessly; the app is
a GUI that builds the same `VideoScript` and hands it to the same renderer.

```
npm start        → Electron window  ─┐
python app.py    → browser tab      ─┴→ autoeditor/server.py → autoeditor/ffmpeg_ops.py
python script.py → terminal          ──────────────────────↗
```

## Pieces

```
app.py                   — starts the server, opens a browser
package.json             — Electron manifest; `npm start` runs desktop/launch.js
desktop/launch.js        — re-spawns Electron with a clean env, then main.js
desktop/main.js          — spawns the Python backend on a free port, opens the window
ui/index.html            — the four pages
ui/app.css               — dark theme
ui/app.js                — all UI behaviour, no framework
autoeditor/server.py     — stdlib HTTP backend, JSON API + media streaming
autoeditor/project.py    — VideoScript ⇄ the JSON the UI exchanges
autoeditor/script_writer.py — VideoScript → markdown (Export and Save As)
autoeditor/sysmon.py     — CPU/GPU sampling for the render page's graph
autoeditor/timecode.py   — `1:02:03.5` ⇄ seconds
```

## Pages

| Page | What it is for |
|---|---|
| **Project Settings** | Template load/save, output file and format, global edits, join defaults, opt-in Auto-Editor passes |
| **Clips** | What is in the video and in what order — add, reorder, remove, relink, per-clip join, level and silence |
| **Edit** | One clip at a time: preview, regions, level, joins |
| **Render** | Summary, the render itself, pipeline status, log, utilisation, markdown export |

Settings and Clips were one page. They split because the Clips page is where per-clip
work accumulates, and it needs the width; Project Settings is a form you fill in once
per project and leave alone.

## Why launch.js exists

VS Code and every Electron-based terminal export `ELECTRON_RUN_AS_NODE=1`. Inherited by
`npm start`, it makes Electron execute `main.js` as plain Node, where `require("electron")`
returns a path string instead of the API and startup dies on `app.whenReady()`.
`launch.js` runs under plain Node — where that same require usefully yields the executable
path — strips the variable and re-spawns. Without it, `npm start` fails in VS Code's
terminal but works elsewhere, which is a confusing bug to chase.

## Backend API

All JSON except the two media routes. Bound to `127.0.0.1` only.

| Route | Purpose |
|---|---|
| `GET /api/templates` | The `.md` files in `templates/`, plus that folder's path |
| `GET /api/template?path=` | Parse one, **non-strict** — missing files come back flagged, not fatal |
| `GET /api/browse?path=` | Folder listing: subfolders plus probed video files |
| `GET /api/probe?path=` | Duration, size, fps, audio, and whether a browser can play it |
| `GET /api/check-output?path=` | Validates an output path before rendering |
| `GET /media?path=` | The video, with **Range** support so seeking works on large files |
| `GET /thumb?path=&t=` | One JPEG frame, `-ss` before `-i` so it stays fast |
| `GET /api/render?since=` | Render job status: state, stages, samples, and log lines after `since` |
| `POST /api/render` | Start a render from a project |
| `POST /api/render/cancel` | Terminate the running ffmpeg |
| `GET /api/balance-audio` | Loudness measurement job: state, per-clip results, progress |
| `POST /api/balance-audio` | Start measuring |
| `POST /api/balance-audio/cancel` | Stop the running measurement |
| `POST /api/save-template` | Save As: write the project into `templates/` (or a given path) |
| `POST /api/export` | Write the project as markdown at an explicit path |
| `POST /api/reveal` | Open the output folder |

`save-template` and `export` write the same file through the same `to_markdown`; they
differ only in where they default to. A bare name given to `save-template` is sanitised
and lands in `templates/`, so the Save As box cannot escape that folder by accident —
a name with a separator in it is honoured as a path.

## Project Settings

The **Global Edits** panel holds the opening and closing fades and `audio adjust`, the
project-wide level. Those are edits to the whole video; they are not output *format*, and
putting them under Output implied they were. Everything about how one clip meets the next
lives on the Edit page instead, which is where that decision is actually made.

`audio adjust` reaches ffmpeg as a `volume=NdB` in each clip's audio chain — added to that
clip's own trim, applied before any crossfade, so blends still sound like blends.

## Relinking

A template outlives the paths in it: footage moves to another drive, a folder is
renamed, an episode is rebuilt from last week's. `parse_script(strict=False)`
already keeps those clips as `missing=True` placeholders rather than refusing to
open the file — relinking is what lets you do something about it.

Every row on the Clips page has **Relink**, amber when that clip's file is
missing, and a banner counts them. It opens the ordinary file picker in relink
mode: single-choice, titled with the clip and the path it used to have, and
already browsing the folder that path pointed at, since a rename is usually a
sibling of where you were.

The part that matters is the checkbox underneath. Moving a drive breaks every
path at once, so when a folder is on screen the picker matches the *other*
missing clips against it by filename and offers to bring them along —
re-pointing twelve clips is one action, not twelve. `relinkClip` keeps each
clip's label, joins, level and regions, only clipping regions that ran past a
shorter replacement's end.

## Encoders

`GET /api/encoders` reports each choice with `available`, found by actually opening the
encoder on a tiny synthetic clip and cached per process. A hardware encoder can be compiled
into ffmpeg and still fail on a machine without that card — the dropdown used to offer AMD
and Intel encoders unconditionally, and the only sign of a wrong pick was a failed render.
Unavailable ones are now disabled in the list, and an encoder named by a template that this
machine lacks is kept selected and flagged rather than silently swapped.

The note under the dropdown says whether the render is on the GPU, and when it is not, which
hardware encoder would be faster. It also says the CPU will not idle, because it won't:
decoding and the transitions are CPU work whatever encodes the output.

`quality` is CRF-like on every encoder — lower is bigger and better — and empty means that
encoder's own default, shown as the field's placeholder.

## The Auto-Editor panel

Silence trimming is not a setting you must have an opinion about — it is a pass
that only runs if you switch it on. So it lives in a `<details>` panel that
starts folded, with the header carrying its state (`off`, `trim silence: 3
clip(s)`) so a glance is enough. `revealAutoEditorIfUsed` unfolds it when a
loaded project actually uses any of it — the default checked, any clip trimming,
or any detection value moved off `blankProject()`'s. It is named for the passes
rather than for silence because it is where the next one will go.

**Balance clip levels** is the first of those, and it is a project setting rather than a
button: ticking it levels what is already in the timeline *and* every clip added afterwards,
so the audio you trim against on the Edit page is already balanced. `POST /api/balance-audio`
measures integrated loudness (scoped to each clip's regions, for the same reasons silence
detection is) and returns the trim that lands it on the target — -14 LUFS by default, what
YouTube normalises to.

The value goes in `clip.balance_db`, kept apart from the manual `audio_gain_db` so the two
never fight and neither is applied twice. `balanceDb()` returns it only while the option is
on, so unticking stops it being applied without discarding the measurements. The preview
gain, the clip listings and the render all read the same sum, so what you hear while
trimming is what gets rendered.

`probe_script` measures anything still unmeasured at render time, which is what makes the
setting mean something from the CLI as well. A value already written into the script is
trusted rather than measured again, so the work is done once.

The measured trim shows on the Clips page beside each clip's manual trim, in the Edit rail
and in the render summary, all off the one `balanceDb()` helper — so switching the option on
updates every view at once rather than only the panel that ran it.

**It runs as a job, not inside the request.** Reading a two-hour recording's audio is a real
wait, and a request can only say "measuring" and hope. `BalanceJob` runs on a background
thread with the same shape as `RenderJob` — poll for state, cancel by terminating the
ffmpeg it holds.

Progress is **weighted by how much audio each clip contributes**, not by clip count: one
two-hour recording among three short ones would otherwise sit at 0% and then finish. And it
is not merely per-clip — `measure_loudness` reports the `pts_time` that every analysis block
already carries, so a single long clip fills the bar smoothly rather than jumping. No extra
ffmpeg flags were needed for that; the position was already in the output being parsed.

A reload mid-run picks the bar back up (`adoptRunningBalance`). Only a *running* job is
adopted — a finished or cancelled one belongs to whoever started it, and reporting it on a
page that never asked would put someone else's result in front of you.

The panel lists what it measured and flags anything it had to limit — a clip too quiet to
reach the target without +24 dB is reported rather than silently amplified into noise.

## Density

Project Settings is a form you fill in once and then read at a glance, so it is packed: no
explanatory paragraphs (the labels carry it), and CSS multi-column rather than grid. Grid
rows align to the tallest panel in the row and leave dead space under the short ones;
columns pack them against each other. Only genuinely dynamic notes remain — the encoder
availability line, the output-path check, the levelling result.

## Removing clips

`removeClip` keeps the removed clip for one undo, offered in the toast and bound to Ctrl+Z,
because a clip carries regions that are slow to mark again. The Edit page has a **Remove
clip** button beside the join controls, and `Delete` removes the selected clip — but only
when no region is highlighted, since `Delete` already means "remove this region" and the
region is the more specific target.

## Sound across a join

The Edit page's second audio bar is the join's, and only appears from the second
clip on. **Overlap** is how long the sound takes to change hands; **lead** is how
far before the picture cut it does so. Left empty, sound follows picture and the
renderer never leaves its ordinary path.

An overlap is played from the clips' own source either side of the cut, so a clip
used to its last frame has nothing to give. `overlapReport` mirrors the
renderer's arithmetic against the clip's regions and duration, so the bar says
`Only 1.00s of 2.00s available` *before* you render rather than leaving you to
wonder why nothing changed. It is marked an estimate when silence trimming is on,
since that only ever frees up more. `audio_notes` prints the same finding into
the render log.

## Non-strict parsing

`parse_script(path, strict=False)` is what the app loads templates with. Missing files
become `TimelineClip(missing=True)` placeholders instead of raising, and a bad output name
is left alone rather than rejected — so a template written on another machine still opens
and can be re-pointed. Rendering always re-validates: `RenderJob` calls
`check_output_path`, and the UI refuses to start with any clip still marked missing.

## Regions

The Edit page's blue bands are `TimelineClip.regions` — a list of `Region(start, end,
join, join_duration)`. `join` is how a region attaches to the one before it *within the
same clip*.

Regions joined by plain cuts reach ffmpeg through the existing `keep_intervals` machinery:
`probe_script` clamps them to the real duration and, when `trim silence` is also on,
**intersects** them with the detected loud regions. One ffmpeg input, no renderer changes.

A region that crossfades or fades into the previous one cannot be expressed that way, so
`expand_regions` splits that clip into one clip per region, promoting each region's join to
a clip join. The ordinary grouping and xfade machinery then handles it with no new cases.
`probe_script` and `render_script` both work off the expanded list, so anything zipping
clips against `ClipInfo`s must expand too — `RenderJob` does.

### Scrubber interaction

One pointer handler on `#scrubber` covers every gesture, so the video seeks live throughout
and the filmstrip never starts a native image drag (it is `pointer-events: none` and
`draggable = false`; that was the original bug where dragging grabbed the thumbnail).

| Gesture | Result |
|---|---|
| drag empty track | scrub, seeking live |
| drag a region body | move it, clamped to its neighbours |
| drag a region edge | resize that edge |
| click a region | select it, for resizing or `Delete` |
| drag while marking | create a region, committed on release |
| `[` then `]` | mark from the playhead; commits immediately |
| wheel | zoom around the pointer |
| shift + wheel | pan |
| `+` / `-` / `0` | zoom in, out, fit |

Marking mode paints the scrubber border amber and shows a hint line, so it is obvious the
next drag creates rather than scrubs. `Esc` cancels.

Constraints live in `regionBounds` (a region may only occupy the gap between its
neighbours) and `MIN_REGION` (0.25s). Every resize, move, create and typed timecode goes
through them, so regions cannot invert, overlap or escape the clip. `_resolve_regions` in
the parser enforces the same rules on markdown input.

Exported markdown writes them as `-- 2:10-5:30, crossfade 0.5, 8:00-12:45`, which parses
back to the same edit — a UI render and a CLI render of the export produce identical
output.

### Zoom

`state.view` is the window of the clip the scrubber shows; `null` means the whole clip.
Everything drawn on the track — filmstrip, region bands, ruler, playhead — is positioned
against `view()` rather than the duration, and `positionToTime` reads gestures back out of
it, so no gesture code knows about zoom at all. `MIN_SPAN` (0.5s) is the floor, which on a
long stream is a few hundred times magnification.

Below the track, the **overview** strip always shows the whole clip with the visible window
drawn on it: drag the window to pan, drag either edge to zoom, click the empty track to
jump. The zoom slider beside it is logarithmic between 1× and `duration / MIN_SPAN`, so its
travel is even at every scale. Wheeling over the track zooms about the pointer, keeping the
frame under the cursor still.

Filmstrip rebuilds are debounced by 140 ms and keyed on `path|start|span`, so a wheel-spin
through a dozen zoom levels asks the backend for one set of thumbnails, not a dozen.
Playback pans the window when the playhead would leave it.

### Seeking, and keeping the two streams together

Seeking is coalesced through `seekTo`: one pending target, applied on `requestAnimationFrame`
and re-applied on `seeked`, so dragging never queues work the decoder cannot keep up with.

`fastSeek` is what makes scrubbing feel live, but it lands on the nearest keyframe and
resolves audio and video separately — which is how a preview ends up sounding a beat
behind the picture, and why the drift "fixes itself" after a tab switch reloads the
decoder. So `fastSeek` is used *only* while a gesture is in flight. Every gesture ends
(`pointerup`), and every playback starts (`play`, `playRegion`), with `settleSeek()`: an
exact `currentTime` assignment onto the frame the gesture asked for. `lastTarget` records
that frame, because reading the position back off the element would return wherever
`fastSeek` landed, not where the user pointed.

Region playback stops on media time inside the `requestAnimationFrame` ticker, not on a
`setTimeout`. A wall-clock timer drifts against the media clock the moment the decoder
stalls, which made a region's end land somewhere different each time it was played.

Leaving the Edit page, and hiding the window, both call `stopPlayback()`.

### Preview audio

The preview element feeds a Web Audio `GainNode`, so the dB you dial in is the level you
hear — `video.volume` alone caps at 1.0 and cannot preview a boost. The node is built
lazily on first play, because an `AudioContext` starts suspended until a user gesture. If
Web Audio is unavailable the code falls back to `video.volume`, previews cuts correctly,
and says so next to the control. Either way the render is unaffected: the gain that
matters is the `volume` filter in the graph.

## Render feedback

`RenderJob` runs one render on a background thread, holding the `Popen` (via
`render_script`'s `on_start` hook) so cancel can terminate it.

**Stages.** Most of a long render's wall clock is spent before a single frame is encoded —
probing, and silence detection, which is two audio passes per clip. A percentage bar has
nothing to say during that. `STAGES` names the six steps the job actually walks, each
carrying a state, a live detail line (`3 / 40 — detecting silence in game-2.mp4`) and how
long it took, so a render that looks stuck can be read at a glance.

**Log.** `on_stderr` streams ffmpeg's own output into the log as it arrives, tagged
`ffmpeg` and dimmed apart from the app's own `app` lines; the checkbox above hides it.
`_FFMPEG_NOISE` drops the lines ffmpeg repeats once per input. Polling passes
`?since=` and gets back only the lines it has not seen, so a chatty encode does not
re-send its whole log four times a minute. Both sides cap at 4000 lines.

**Progress.** The bar is blue while running, green when the render finishes and red when it
fails or is cancelled, so a glance at the window says whether it worked. A new render clears
the colour before it starts, so the previous run's outcome is never mistaken for this one's.

**Utilisation.** `sysmon.Sampler` records CPU, GPU and **encoder** load once a second, and
the graph draws all three over 0-100%. The encoder trace exists because its absence was
actively misleading: NVENC is fixed-function silicon separate from the shaders, so a card
encoding flat out reports `utilization.gpu` in the low teens. Measured during an nvenc
render here: CPU 78%, GPU 34%, encoder 99% - without the third trace that reads as an idle
GPU while the CPU does all the work, which is the opposite of what is happening. It is deliberately secondary: it answers "is the
encoder actually working, and is it on the GPU I selected", which is the question you have
when a render is slower than expected. GPU needs `nvidia-smi` on PATH; without it the trace
is absent and a note says why, rather than drawing a flat zero that reads as an idle card.

## Preview limits

The preview window decodes the file itself, and it decodes less than ffmpeg does. Two
separate questions decide whether a clip previews, and they are answered in different
places on purpose.

**The container** is a fixed property of the build: which demuxers Chromium ships does not
vary by machine. `_probe_summary` answers that server-side against `PLAYABLE_CONTAINERS`
and puts the reason in `preview_note`. `.avi` fails here (`DEMUXER_ERROR_COULD_NOT_OPEN`);
`.mkv` does not, and used to be refused for no reason because the check was on the file
extension rather than on anything real.

**The codec** is a property of the machine. Chromium ships no software HEVC decoder — it
decodes HEVC through the platform's hardware path or not at all — so the same `.mp4`
previews on a workstation and fails on a laptop, and a GPU-accelerated window and a
software-rendered one disagree on the very same box. No list written on one computer can
answer this, so the UI asks its own decoder: `codecSupported` probes `canPlayType` and
`MediaSource.isTypeSupported` with a representative type per codec, and returns `null` for
codecs it has no probe for, which are simply attempted.

That distinction is not academic. HEVC on a machine that cannot decode it **fails
silently**: the element fires `loadeddata`, reports `readyState` 4, sets no error, and
renders a 0×0 frame. The symptom is a black rectangle with a dead-looking transport — which
reads as broken seeking, not as an unsupported codec.

So the capability probe is a first answer and the element is the last word. Two handlers
catch whatever the probe misses:

| Handler | Catches |
|---|---|
| `checkPreviewDecoded` on `loadeddata` | a clean load with `videoWidth === 0` — the silent case |
| `error` | a refusal, reported with the browser's own `MediaError.message` |

Both fall through to `showFallback`, which names the codec and says regions still work and
the render is unaffected — true, and worth saying, because an editor facing a black preview
will otherwise assume the clip itself is broken. `showFallback` hides the element *before*
clearing `src`, since clearing it fires an `error` of its own.

Unplayable clips still render normally and can still be given regions from the filmstrip
and the timecode fields. Transcoding proxies would remove the limit entirely and remain the
obvious next step.

The preview is sized to the clip and centred, the black area being the picture itself
rather than a page-width letterbox — so 4:3 and portrait sources are not marooned in the
middle of a slab.

## Estimated length

The UI's duration estimate sums kept regions minus crossfade overlaps. It cannot know what
silence trimming will remove without running detection, so when any clip has `trim silence`
the figure is labelled "up to … (before silence trimming)" rather than shown as exact.
