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
desktop/main.js          — starts the backend on a free port, opens the window
ui/index.html            — the four pages
ui/app.css               — dark theme
ui/js/                   — ES modules, one per concern
```

The backend is described in [architecture.md](architecture.md); this file is about the
front end and the API between them.

## The front end, module by module

`index.html` loads `js/main.js` as a module, so every file below keeps its own scope.

| Module | Owns |
|---|---|
| `util.js` | `$`, `el`, formatting, `toast`, the fetch wrappers |
| `library.js` | the effect and transition registry, fetched from the engine |
| `state.js` | the project being edited, the probe cache, and `layout()` |
| `pages.js` | which page is showing |
| `settings.js` | the settings form, the output path, encoders, opening and saving |
| `balance.js` | the levelling job and its panel |
| `picker.js` | the browse modal: adding clips, relinking, `choosePath` |
| `views.js` | what every page renders in common, and removal with undo |
| `clips.js` | the Clips page table, grouped by track |
| `timeline.js` | the Edit page: lanes, clip blocks, transitions, drag, zoom |
| `inspector.js` | controls for whatever is selected, built from the library |
| `tracklanes.js` | adding, removing and ordering the tracks themselves |
| `preview.js` | the video element: following the playhead across the edit |
| `render.js` | the Render page: stages, log, utilisation, progress |
| `main.js` | imports everything, then starts it |

Two rules keep that honest. A module owns its own state and exposes functions to change it —
`preview.js` grew `resetSeekTarget()` for exactly this reason, because ES module imports are
const bindings and another module was assigning to one. And what a module exports is what
another module needs, plus the operations that make sense to ask of it; nothing is exported
only to be seen.

`main.js` puts the namespaces on `window.app`. That is a deliberate seam: the app runs in a
window with no devtools and no address bar, so it is otherwise impossible to look at while
running, and impossible for a test to drive. `window.app.modules` holds the namespace
objects, whose bindings stay live; the flattened copy beside them is a snapshot, which is
right for functions and wrong for anything that changes. Tests opt into globals with
`Object.assign(window, window.app)` — the app itself never does.

## Pages

| Page | What it is for |
|---|---|
| **Project Settings** | Template load/save, output file and format, global edits, join defaults, opt-in Auto-Editor passes |
| **Clips** | Every clip grouped by track — add, reorder, remove, relink, level and silence in bulk |
| **Edit** | The timeline: lanes, clips, transitions, and an inspector |
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
| `GET /api/library` | The effect and transition registry the inspector builds its controls from |
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
| `POST /api/reveal` | Open the output folder (browser fallback; see below) |

`save-template` writes the project through `to_markdown`. A bare name is sanitised and
lands in `templates/`; a name with a separator in it is honoured as a path, which is what
Save as… sends.

## Project Settings

Three panels, and each answers one question.

**Project** — which script is open, and where changes go. The *Editing* line names it, and
is the only thing that claims to; the dropdown beside it is a chooser, not a status
display, which is why it falls back to its first entry when the open project is not one of
the listed templates. **Save** overwrites what is open and is disabled until there is
something to overwrite; **Save as…** picks a folder and a name.

This is also where a project becomes a script, which used to be a separate *Export* box on
the Render page. That box wrote the same file through the same endpoint, but it started
empty with only a placeholder for guidance — so clicking it did nothing but print a small
red line, which reads as broken. One saving control, always pointed somewhere real.

**Output** — a folder, a name and a container, rather than one long path to type correctly.
The model still keeps a single path, because that is what a script records and what ffmpeg
is handed; `splitOutput`/`outputFromForm` are the only two places that translate. Splitting
it is also what puts the path check right beside the field that broke it, which matters
because Windows silently turns `title: subtitle.mp4` into a 0-byte file. `.mp4`, `.mov` and
`.mkv` all take h264/aac; `+faststart` is added only for the mp4-family containers that
have an index to move.

**Auto-Editor** — two opt-in passes and one group of ordinary settings, as fieldsets rather
than a collapsing panel. Ticking a pass enables its fields and dims them when off, so the
panel shows what is available as well as what is running; *Clip joining* is never disabled
because it always applies. The opening and closing fades live there: they are transitions
to and from black, which is the same question as how one clip meets the next.

There is no Global Edits panel any more. Its `audio adjust` set every clip's level by
guess, which levelling now does by measurement — keeping both would have been two controls
for one outcome, applied one after the other.

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

## Revealing the output

`shell.showItemInFolder` runs in the Electron shell, reached through the one bridge in
`desktop/preload.js`. It has to: Windows only lets the *foreground* process pass focus on,
and that is the shell, not the backend it spawned — a folder opened from Python lands behind
the app window, which is exactly what it did.

`window.desktop` is absent in a browser, so the UI falls back to `POST /api/reveal` and
`library.reveal`, which uses `explorer /select,<file>` to open the folder with the file
already highlighted.

## Removing clips

`removeClip` keeps the removed clip for one undo, offered in the toast and bound to Ctrl+Z.
It takes the transition that came with it, so undo restores the whole join rather than
dropping the clip back in as a hard cut. The inspector has a **Remove clip** button, and
removing an entry also drops any transition left with nothing on one side of it — which
can no longer mean anything.

## Sound across a join

Select a transition on the timeline and the inspector offers **Length** and
**Lead** under *Sound*. Both blank means the sound changes hands with the
picture, and the renderer never leaves its ordinary path. Length is how long the
handover takes; lead is how many seconds before the picture cut it happens —
positive is a J-cut, negative an L-cut.

Because audio has its own lane, an offset is also visible rather than only
numeric: the transition is drawn twice, once over the picture's overlap and once
where the *sound* actually crosses, so a J-cut looks like the offset it is.
Shift-dragging a transition moves the sound alone.

An overlap is played from the clips' own source either side of the cut, so a clip
used to its last frame has nothing to give. `audio_notes` reports any shortfall
into the render log.

## Non-strict parsing

`parse_script(path, strict=False)` is what the app loads templates with. Missing files
become `TimelineClip(missing=True)` placeholders instead of raising, and a bad output name
is left alone rather than rejected — so a template written on another machine still opens
and can be re-pointed. Rendering always re-validates: `RenderJob` calls
`check_output_path`, and the UI refuses to start with any clip still marked missing.

## The timeline

The Edit page is one lane per track, with the horizontal axis being the finished
video. That is the axis on which a transition is a thing you can see and point
at; the old page showed a rail of clips and a scrubber spanning one clip's
*source*, which could say what was inside a clip but never how clips sat against
each other.

### What draws where

`state.js`'s `layout(track)` returns each clip's start and length, and it applies
the **same rule** as `Track.laid_out` in the engine. Anything drawn from a
different rule would be a lie about what will render, so the two are checked
against each other over every fixture (`scratchpad/layout_cross.*`), and the
geometry a transition needs — whether it overlaps, whether it trims the incoming
clip — travels in `/api/library` rather than being reimplemented here.

Everything that maps time to pixels measures `.tl-scale`, which covers exactly
the striped area; the lane heads take a fixed column and are not part of the time
axis.

### Gestures

| Gesture | Result |
|---|---|
| Click a clip or transition | Select it; the inspector follows |
| Click empty ground | Move the playhead there and preview it |
| Drag a clip | Reorder it among its track's clips |
| Drag a clip onto another lane | Move it there, pinned where you dropped it |

A clip dropped on another lane arrives **pinned**. It has no place in that
lane's running order, and guessing a slot for it would shift everything else
there — so it keeps the time it was dropped at, and can be unpinned from the
inspector to fall back into line. Any transition left joining nothing is
dropped with it.
| Alt-drag a clip | Pin it to a time of its own |
| Drag a clip's edge | Trim its in or out point |
| Drag a transition | Change its duration |
| Shift-drag a transition | Move the *sound* — lead it or lag it |
| Wheel | Zoom about the pointer |

### The inspector

Built from `/api/library`: an effect's own parameters decide its inputs, their
units and their bounds. Adding an effect to `effects.py` gives it a control in
the app without `inspector.js` knowing its name — which is the point of having a
library rather than a fixed set of fields.

### Tracks

Video tracks stack in the order they appear, first at the bottom. Each lane head
carries its name, a visibility toggle (video) or mute (both), and a gain that
applies to everything on it. The last video track cannot be removed — there
would be nothing to render.

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

A clip that cannot be previewed still renders normally, and the timeline still shows where
it sits — only the picture is missing. An audio-only source (a music bed) is probed for its
length like any other clip and reports "this file has sound but no picture" rather than
failing.

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

The estimate is `projectDuration()`, which runs the same layout the compositor does and
takes the longest **video** track — a music bed outlasting the last frame is cut off, not an
extension of the edit. It cannot know what silence trimming will remove without running
detection, so when any clip carries `trim silence` the figure is labelled
"up to … (before silence trimming)" rather than shown as exact. That is also the one case
where the app's layout and the engine's legitimately differ, and why the cross-check skips
those fixtures rather than pretending to agree.
