"use strict";

// ---------------------------------------------------------------- state

const state = {
  project: blankProject(),
  selected: -1,     // index into project.clips
  probes: {},       // path -> {duration, has_audio, playable, ...}
  marking: false,     // armed to mark a new region
  markStart: null,    // "[" set, waiting for "]"
  activeRegion: -1,   // selected region index
  drag: null,         // in-flight pointer gesture
  view: null,         // {start, end} window the scrubber shows; null = whole clip
  browse: { path: "", files: [], chosen: new Set(), loaded: false, relink: -1 },
  picker: null,      // in-flight choosePath() request
  template: "",      // path of the script this project was opened from
  page: "settings",
  poll: null,
  render: { log: [], count: 0, samples: [], hasGpu: false },
};

function blankProject() {
  return {
    title: "Untitled",
    output: { file: "", resolution: "1920x1080", fps: 60, encoder: "libx264", quality: null },
    defaults: {
      join: "crossfade", crossfade: 0.3, fade: 0.5, trim_silence: false,
      fade_in: 0.5, fade_out: 0.5, audio_overlap: null, audio_lead: 0,
    },
    silence: { threshold_db: -30, padding: 0.5, min_silence: 1.0, min_segment: 0.5 },
    balance: { enabled: false, target_lufs: -14 },
    clips: [],
  };
}

// ---------------------------------------------------------------- utils

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

function fmt(seconds) {
  if (!isFinite(seconds) || seconds < 0) seconds = 0;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function fmtPrecise(seconds) {
  const base = fmt(seconds);
  const frac = Math.round((seconds % 1) * 10);
  return frac ? `${base}.${frac}` : base;
}

/** Timecode with enough decimals to be useful at the current zoom. */
function fmtTick(seconds, span) {
  if (span > 120) return fmt(seconds);
  const base = fmt(Math.floor(seconds));
  const digits = span > 12 ? 1 : 2;
  const frac = (seconds % 1).toFixed(digits).slice(1);
  return base + frac;
}

function parseTime(text) {
  const parts = String(text).trim().split(":");
  if (parts.some((p) => p === "" || isNaN(Number(p)))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

function toast(message, bad) {
  const node = $("toast");
  node.textContent = message;
  node.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => (node.className = "toast"), 3200);
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({ error: "bad response" }));
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

const mediaUrl = (path) => "/media?path=" + encodeURIComponent(path);

// ---------------------------------------------------------------- probing

async function probe(path) {
  if (state.probes[path]) return state.probes[path];
  try {
    const info = await api("/api/probe?path=" + encodeURIComponent(path));
    state.probes[path] = info;
    return info;
  } catch {
    return (state.probes[path] = { duration: 0, playable: false, error: "unreadable" });
  }
}

function keptDuration(clip) {
  const info = state.probes[clip.path];
  const total = info ? info.duration || 0 : 0;
  const regions = clip.regions || [];
  if (!regions.length) return total;
  // A crossfade between regions overlaps them; a fade does not shorten anything.
  return regions.reduce((sum, r, i) => {
    const overlap = i > 0 && r.join === "crossfade" ? r.join_duration || 0 : 0;
    return sum + Math.max(0, r.end - r.start) - overlap;
  }, 0);
}

const MIN_REGION = 0.25;
// The tightest the scrubber will zoom: half a second across the whole track.
const MIN_SPAN = 0.5;

function clipDuration(clip) {
  const info = clip ? state.probes[clip.path] : null;
  return (info && info.duration) || 0;
}

/** Bounds a region may occupy: the clip, minus its neighbours. */
function regionBounds(regions, index, duration) {
  const before = regions[index - 1];
  const after = regions[index + 1];
  return [before ? before.end : 0, after ? after.start : duration];
}

function newRegion(start, end) {
  return { start, end, join: "cut", join_duration: 0 };
}

function estimateTotal() {
  let total = 0;
  state.project.clips.forEach((clip, i) => {
    total += keptDuration(clip);
    if (i > 0 && clip.join === "crossfade") total -= clip.join_duration || 0;
  });
  return Math.max(0, total);
}

// Silence trimming only resolves during a render, so the estimate reads high
// whenever any clip opts into it.
function estimateIsUpperBound() {
  return state.project.clips.some((c) => c.trim_silence);
}

function estimateLabel() {
  const text = fmt(estimateTotal());
  return estimateIsUpperBound() ? `up to ${text} (before silence trimming)` : text;
}

function joinLabel(clip) {
  return clip.join === "cut" ? "cut" : `${clip.join} ${clip.join_duration}s`;
}

function audioFollowsPicture(clip) {
  return !clip.audio_lead &&
    Math.abs(clipAudioBlend(clip) - (clip.join_duration || 0)) < 1e-9;
}

function audioJoinLabel(clip) {
  const lead = clip.audio_lead || 0;
  return `audio ${clipAudioBlend(clip)}s` +
    (lead ? ` @ ${lead > 0 ? "+" : ""}${lead}s` : "");
}

// ---------------------------------------------------------------- pages

function showPage(name) {
  // Leaving the Edit page must silence the preview -- audio playing under a
  // page you are not looking at is never what you meant.
  if (state.page === "edit" && name !== "edit") stopPlayback();
  state.page = name;

  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  $("page-" + name).classList.add("active");
  document.querySelectorAll(".step").forEach((b) =>
    b.classList.toggle("active", b.dataset.page === name)
  );
  if (name === "clips") renderClipTable();
  if (name === "edit") renderClipList();
  if (name === "render") renderSummary();
}

document.querySelectorAll(".step").forEach((button) =>
  button.addEventListener("click", () => showPage(button.dataset.page))
);

// A hidden tab, a minimised window or a lock screen stops the preview too.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPlayback();
});

// ---------------------------------------------------------------- settings: form binding

const FIELDS = [
  ["out-resolution", "output", "resolution", "string"],
  ["out-fps", "output", "fps", "int"],
  ["out-encoder", "output", "encoder", "string"],
  ["out-quality", "output", "quality", "opt"],
  ["def-join", "defaults", "join", "string"],
  ["def-crossfade", "defaults", "crossfade", "float"],
  ["def-fade", "defaults", "fade", "float"],
  ["def-fade-in", "defaults", "fade_in", "float"],
  ["def-fade-out", "defaults", "fade_out", "float"],
  ["def-trim", "defaults", "trim_silence", "bool"],
  ["bal-enabled", "balance", "enabled", "bool"],
  ["bal-target", "balance", "target_lufs", "float"],
  ["def-audio-overlap", "defaults", "audio_overlap", "opt"],
  ["def-audio-lead", "defaults", "audio_lead", "float"],
  ["sil-threshold", "silence", "threshold_db", "float"],
  ["sil-padding", "silence", "padding", "float"],
  ["sil-min-silence", "silence", "min_silence", "float"],
  ["sil-min-segment", "silence", "min_segment", "float"],
];

function formToProject() {
  for (const [id, section, key, kind] of FIELDS) {
    const node = $(id);
    const raw = kind === "bool" ? node.checked : node.value;
    state.project[section][key] =
      kind === "int" ? parseInt(raw, 10) || 0 :
      kind === "float" ? parseFloat(raw) || 0 :
      kind === "opt" ? (String(raw).trim() === "" ? null : parseFloat(raw) || 0) :
      raw;
  }
}

function projectToForm() {
  for (const [id, section, key, kind] of FIELDS) {
    const node = $(id);
    const value = state.project[section][key];
    if (kind === "bool") node.checked = !!value;
    else if (kind === "opt") node.value = value === null || value === undefined ? "" : value;
    else node.value = value;
  }
  $("project-title").textContent = state.project.title || "";
  outputToForm();
  showProjectName();
  showGroupStates();
}

/** Which script this project came from, and whether Save has a target. */
function showProjectName() {
  const name = state.template
    ? state.template.split(/[\\/]/).pop().replace(/\.md$/i, "")
    : "";
  $("project-current").textContent = name || `${state.project.title || "Untitled"} — not saved yet`;
  $("save-template").disabled = !state.template;
  $("save-template").title = state.template
    ? `Overwrite ${state.template}`
    : "Use Save as… first; this project has no file yet";
}

/** Opt-in groups read as available or not, rather than merely unticked. */
function showGroupStates() {
  [["group-balance", "bal-enabled"], ["group-silence", "def-trim"]].forEach(([group, box]) => {
    const on = $(box).checked;
    $(group).classList.toggle("off", !on);
    $(group).querySelectorAll(".group-body input, .group-body select, .group-body button")
      .forEach((node) => (node.disabled = !on));
  });
}

FIELDS.forEach(([id, section, key]) =>
  $(id).addEventListener("change", () => {
    formToProject();
    if (id === "out-encoder" || id === "out-quality") showEncoderNote();
    if (key === "trim_silence") showAutoEditorState();
  })
);

// ---------------------------------------------------------------- output path
//
// The model keeps one path, because that is what a script records and what
// ffmpeg is handed. The form splits it into the three things a person actually
// decides -- where, what it is called, and what kind of file it is.

function splitOutput(full) {
  const text = String(full || "");
  const cut = text.replace(/[\\/][^\\/]*$/, "");
  const folder = cut === text ? "" : cut;
  const base = text.slice(folder.length).replace(/^[\\/]/, "");
  const dot = base.lastIndexOf(".");
  return dot > 0
    ? { folder, name: base.slice(0, dot), ext: base.slice(dot).toLowerCase() }
    : { folder, name: base, ext: ".mp4" };
}

function outputToForm() {
  const { folder, name, ext } = splitOutput(state.project.output.file);
  $("out-folder").value = folder;
  $("out-name").value = name;
  const formats = [...$("out-format").options].map((o) => o.value);
  $("out-format").value = formats.includes(ext) ? ext : ".mp4";
  $("render-out-file").textContent = state.project.output.file || "(not set)";
}

function outputFromForm() {
  const folder = $("out-folder").value.trim().replace(/[\\/]+$/, "");
  const name = $("out-name").value.trim();
  const ext = $("out-format").value;
  state.project.output.file = name ? (folder ? `${folder}\\${name}${ext}` : name + ext) : "";
  $("render-out-file").textContent = state.project.output.file || "(not set)";
  checkOutput($("out-check"));
}

["out-folder", "out-name", "out-format"].forEach((id) =>
  $(id).addEventListener("change", outputFromForm)
);

$("browse-output").addEventListener("click", async () => {
  const chosen = await choosePath({
    title: "Choose the output folder",
    start: $("out-folder").value || state.browse.path,
  });
  if (!chosen) return;
  $("out-folder").value = chosen.folder;
  outputFromForm();
});

// ---------------------------------------------------------------- auto-editor
//
// Two opt-in passes and one group of plain settings. The passes stay switched
// off until asked for, and their fields go dead with them, so the panel says
// what is running without hiding what is available.

function showAutoEditorState() {
  const p = state.project;
  const bits = [];
  if (p.balance.enabled) bits.push(`balancing to ${p.balance.target_lufs} LUFS`);
  if (p.defaults.trim_silence) bits.push("trimming silence");
  else {
    const clips = p.clips.filter((c) => c.trim_silence).length;
    if (clips) bits.push(`trimming silence on ${clips} clip(s)`);
  }
  $("autoeditor-state").textContent = bits.join("  ·  ");
  showGroupStates();
}

async function checkOutput(target) {
  const path = state.project.output.file;
  if (!path) {
    target.textContent = "";
    return;
  }
  try {
    const result = await api("/api/check-output?path=" + encodeURIComponent(path));
    if (!result.ok) {
      target.textContent = result.error;
      target.className = "note bad";
    } else if (result.exists) {
      target.textContent = "A file already exists here and will be overwritten.";
      target.className = "note warn";
    } else if (!result.folder_exists) {
      target.textContent = "Folder does not exist yet; it will be created.";
      target.className = "note";
    } else {
      target.textContent = "Output path looks good.";
      target.className = "note ok";
    }
  } catch (err) {
    target.textContent = err.message;
    target.className = "note bad";
  }
}

// ---------------------------------------------------------------- balance levels
//
// Clips recorded in different sessions rarely match, and matching them by ear
// one at a time is the tedious part of assembling an episode.
//
// The measured trim lives in `balance_db`, apart from the manual `audio_gain_db`
// so the two never fight and neither is applied twice. Switching the option on
// measures what is already here and every clip added afterwards, so the preview
// you trim against is already levelled; the render measures anything still
// unmeasured, so a script that never met the app still comes out level.

function showBalanceResults(rows) {
  const box = $("balance-results");
  box.innerHTML = "";
  rows.forEach((row) => {
    const line = el("div", "balance-row" + (row.gain === null ? " skipped" : ""));
    line.appendChild(el("span", "who", `${row.index + 1}. ${row.label}`));
    line.appendChild(el("span", "was", row.lufs === undefined ? "" : `${row.lufs} LUFS`));
    line.appendChild(el("span", "gain",
      row.gain === null ? "-" : `${row.gain > 0 ? "+" : ""}${row.gain} dB`));
    line.appendChild(el("span", "why", row.note || ""));
    box.appendChild(line);
  });
}

let balancing = false;
let balancePoll = null;

function showBalanceProgress(status) {
  const running = status && status.state === "running";
  $("balance-progress").classList.toggle("hidden", !running);
  if (!running) return;
  $("balance-bar").style.width = (status.pct || 0) + "%";
  // Totals are only known once every clip has been probed.
  $("balance-progress-text").textContent = status.total_seconds
    ? `${Math.round(status.pct)}%  ${fmt(status.done_seconds)} / ${fmt(status.total_seconds)}` +
      (status.current ? `  ·  ${status.current}` : "")
    : "Looking at the clips…";
}

/**
 * Measure and level. `onlyUnmeasured` limits it to clips not yet measured,
 * which is what adding a clip to an already-levelled project should do.
 *
 * The work happens in a job on the backend rather than inside the request:
 * reading a two-hour recording's audio is a real wait, and a request can only
 * say "measuring" and hope, where a job can say how far it has got and be
 * called off.
 */
async function runBalance({ onlyUnmeasured } = {}) {
  if (balancing) return;
  const note = $("balance-note");
  const pending = state.project.clips.filter(
    (c) => !onlyUnmeasured || c.balance_db === null || c.balance_db === undefined
  );
  if (!pending.length) {
    if (!onlyUnmeasured) {
      note.textContent = "Add some clips first.";
      note.className = "note bad";
    }
    return;
  }

  balancing = true;
  $("balance-run").disabled = true;
  note.textContent = `Measuring ${pending.length} clip(s)…`;
  note.className = "note";

  const target = state.project.balance.target_lufs;
  try {
    await post("/api/balance-audio", {
      project: state.project,
      target,
      only_unmeasured: !!onlyUnmeasured,
    });
  } catch (err) {
    note.textContent = err.message;
    note.className = "note bad";
    balancing = false;
    $("balance-run").disabled = false;
    return;
  }

  clearInterval(balancePoll);
  balancePoll = setInterval(() => pollBalance(target), 400);
  pollBalance(target);
}

async function pollBalance(target) {
  let status;
  try {
    status = await api("/api/balance-audio");
  } catch {
    return;
  }
  showBalanceProgress(status);
  if (status.state === "running") return;

  clearInterval(balancePoll);
  balancing = false;
  $("balance-run").disabled = false;
  const note = $("balance-note");

  if (status.state === "cancelled") {
    note.textContent = "Measurement cancelled; nothing was changed.";
    note.className = "note warn";
    return;
  }
  if (status.state === "error") {
    note.textContent = status.error;
    note.className = "note bad";
    return;
  }

  let set = 0;
  status.clips.forEach((row) => {
    const clip = state.project.clips[row.index];
    if (!clip || row.gain === null || row.gain === undefined) return;
    clip.balance_db = row.gain;
    set += 1;
  });
  showBalanceResults(status.clips);
  const skipped = status.clips.length - set;
  note.textContent =
    `Levelled ${set} clip(s) to ${target} LUFS` +
    (skipped ? `; ${skipped} could not be measured.` : ".");
  note.className = skipped ? "note warn" : "note ok";
  refreshAll();
  if (state.selected >= 0) syncGainControls();
}

$("balance-cancel").addEventListener("click", () => {
  post("/api/balance-audio/cancel", {}).catch((e) => toast(e.message, true));
});

/**
 * Pick up a measurement already in flight -- a reload mid-run should show the
 * bar rather than sit blank. Only a *running* job is adopted: a finished or
 * cancelled one belongs to whoever started it, and reporting it here would put
 * someone else's result on a page that never asked for it.
 */
async function adoptRunningBalance() {
  let status;
  try {
    status = await api("/api/balance-audio");
  } catch {
    return;
  }
  if (status.state !== "running") return;
  balancing = true;
  $("balance-run").disabled = true;
  showBalanceProgress(status);
  clearInterval(balancePoll);
  const target = state.project.balance.target_lufs;
  balancePoll = setInterval(() => pollBalance(target), 400);
}

$("balance-run").addEventListener("click", () => {
  formToProject();
  state.project.clips.forEach((c) => (c.balance_db = null));
  runBalance();
});

$("balance-reset").addEventListener("click", () => {
  state.project.clips.forEach((clip) => (clip.balance_db = null));
  showBalanceResults([]);
  $("balance-note").textContent = "Levelling cleared; clips play at their own level.";
  $("balance-note").className = "note";
  refreshAll();
  if (state.selected >= 0) syncGainControls();
});

// Switching it on levels what is already here; switching it off leaves the
// measurements in place but stops them being applied.
$("bal-enabled").addEventListener("change", () => {
  formToProject();
  showAutoEditorState();
  showGroupStates();
  if (state.project.balance.enabled) runBalance({ onlyUnmeasured: true });
  else {
    $("balance-note").textContent = "";
    refreshAll();
    if (state.selected >= 0) syncGainControls();
  }
});

$("bal-target").addEventListener("change", () => {
  formToProject();
  if (state.project.balance.enabled) {
    state.project.clips.forEach((c) => (c.balance_db = null));
    runBalance();
  }
});

// ---------------------------------------------------------------- encoders
//
// A hardware encoder can be compiled into ffmpeg and still fail to open on a
// machine without that card. Asking once, at startup, keeps the list honest
// instead of letting a wrong choice surface as a failed render.

let encoders = [];

async function loadEncoders() {
  try {
    encoders = (await api("/api/encoders")).encoders;
  } catch {
    return;
  }
  const select = $("out-encoder");
  const chosen = state.project.output.encoder;
  select.innerHTML = "";
  encoders.forEach((enc) => {
    const option = el("option", null, enc.label + (enc.available ? "" : " — not on this machine"));
    option.value = enc.name;
    option.disabled = !enc.available;
    select.appendChild(option);
  });
  select.value = chosen;
  // A template may name an encoder this machine does not have; keep it visible
  // rather than silently selecting something else.
  if (select.value !== chosen) {
    const ghost = el("option", null, `${chosen} — not on this machine`);
    ghost.value = chosen;
    select.appendChild(ghost);
    select.value = chosen;
  }
  showEncoderNote();
}

function showEncoderNote() {
  const note = $("encoder-note");
  const name = state.project.output.encoder;
  const enc = encoders.find((e) => e.name === name);
  $("out-quality").placeholder = enc ? `default ${enc.default_quality}` : "default";

  if (!encoders.length) {
    note.textContent = "";
    return;
  }
  if (!enc || !enc.available) {
    note.textContent = `${name} is not available here — the render would fail. ` +
      "Pick one that is.";
    note.className = "note bad";
    return;
  }
  if (!enc.hardware) {
    const gpu = encoders.find((e) => e.hardware && e.available);
    note.textContent = gpu
      ? `Encoding on the CPU. ${gpu.name} would render about twice as fast.`
      : "Encoding on the CPU; no hardware encoder is available here.";
    note.className = "note";
    return;
  }
  note.textContent = "Encoding on the GPU.";
  note.className = "note ok";
}

async function loadTemplates(select) {
  try {
    const { templates } = await api("/api/templates");
    const picker = $("template-select");
    picker.innerHTML = "";
    const blank = el("option", null, "Blank project");
    blank.value = "";
    picker.appendChild(blank);
    templates.forEach((t) => {
      const option = el("option", null, t.name);
      option.value = t.path;
      picker.appendChild(option);
    });
    // A project saved outside templates/ has no option here; falling back to
    // the first keeps the control readable instead of blank. Which file is
    // open is the "Editing" line's job, not this one's.
    picker.value = select || "";
    if (picker.selectedIndex < 0) picker.selectedIndex = 0;
  } catch (err) {
    toast(err.message, true);
  }
}

$("load-template").addEventListener("click", async () => {
  const path = $("template-select").value;
  const note = $("template-note");

  if (!path) {
    state.project = blankProject();
    state.template = "";
    state.selected = -1;
    projectToForm();
    showEncoderNote();
    refreshAll();
    note.textContent = "Started a blank project.";
    note.className = "note";
    return;
  }

  try {
    const data = await api("/api/template?path=" + encodeURIComponent(path));
    state.project = data;
    state.template = path;
    state.selected = data.clips.length ? 0 : -1;
    projectToForm();
    showEncoderNote();
    await Promise.all(data.clips.map((c) => probe(c.path)));
    refreshAll();

    const missing = data.clips.filter((c) => c.missing).length;
    note.textContent = missing
      ? `Opened ${data.clips.length} clips — ${missing} file(s) not found. Relink them on the Clips page.`
      : `Opened ${data.clips.length} clips.`;
    note.className = missing ? "note warn" : "note ok";
    checkOutput($("out-check"));
  } catch (err) {
    note.textContent = err.message;
    note.className = "note bad";
  }
});

async function saveProject(path) {
  formToProject();
  const note = $("template-note");
  try {
    const result = await post("/api/save-template", { project: state.project, path });
    state.template = result.path;
    await loadTemplates(result.path);
    showProjectName();
    note.textContent = `Saved to ${result.path}`;
    note.className = "note ok";
    toast("Saved.");
  } catch (err) {
    note.textContent = err.message;
    note.className = "note bad";
  }
}

$("save-template").addEventListener("click", () => {
  if (state.template) saveProject(state.template);
});

$("save-template-as").addEventListener("click", async () => {
  const suggested = state.template
    ? state.template.split(/[\\/]/).pop().replace(/\.md$/i, "")
    : (state.project.title || "my-episode");
  const chosen = await choosePath({
    title: "Save project as",
    start: state.template ? state.template.replace(/[\\/][^\\/]*$/, "") : "",
    filename: suggested,
    extension: ".md",
  });
  if (!chosen) return;
  saveProject(`${chosen.folder}\\${chosen.name}.md`);
});

// ---------------------------------------------------------------- clips: browser

async function browse(path) {
  try {
    const data = await api("/api/browse?path=" + encodeURIComponent(path || ""));
    state.browse.path = data.path;
    state.browse.files = data.files;
    state.browse.chosen.clear();
    updatePickerCount();
    $("browse-path").value = data.path;
    $("browse-crumbs").textContent = data.path;

    const list = $("browse-list");
    list.innerHTML = "";

    if (data.parent) {
      const up = el("div", "entry dir");
      up.appendChild(el("div", "name", ".."));
      up.addEventListener("click", () => browse(data.parent));
      list.appendChild(up);
    }

    data.dirs.forEach((dir) => {
      const row = el("div", "entry dir");
      row.appendChild(el("div", "name", dir.name));
      row.addEventListener("click", () => browse(dir.path));
      list.appendChild(row);
    });

    const relinking = state.browse.relink >= 0;
    const foldersOnly = !!state.picker;

    (foldersOnly ? [] : data.files).forEach((file) => {
      state.probes[file.path] = file;
      const row = el("div", "entry" + (previewProblem(file) ? " no-preview" : ""));
      const box = el("input");
      box.type = relinking ? "radio" : "checkbox";
      box.name = "pick";
      box.addEventListener("click", (event) => event.stopPropagation());
      box.addEventListener("change", () => {
        // Relinking replaces one file, so only one choice can stand.
        if (relinking) state.browse.chosen.clear();
        box.checked ? state.browse.chosen.add(file.path) : state.browse.chosen.delete(file.path);
        document.querySelectorAll("#browse-list .entry").forEach((n) =>
          n.classList.toggle("chosen", state.browse.chosen.has(n.dataset.path))
        );
        updatePickerCount();
      });
      row.dataset.path = file.path;
      row.appendChild(box);
      row.appendChild(el("div", "name", file.name));
      const bits = [];
      if (file.duration) bits.push(fmt(file.duration));
      if (file.width) bits.push(`${file.width}x${file.height}`);
      if (previewProblem(file)) bits.push("no preview");
      if (file.error) bits.push(file.error);
      row.appendChild(el("div", "meta", bits.join("  ·  ")));
      row.addEventListener("click", () => {
        box.checked = !box.checked;
        box.dispatchEvent(new Event("change"));
      });
      list.appendChild(row);
    });

    if (!data.dirs.length && (foldersOnly || !data.files.length)) {
      list.appendChild(el("div", "empty-note",
        foldersOnly ? "No folders inside this one." : "No folders or video files here."));
    }
    if (!foldersOnly) showRelinkRest();
  } catch (err) {
    toast(err.message, true);
  }
}

function updatePickerCount() {
  if (state.picker) {
    const named = !state.picker.wantsName || $("picker-name").value.trim();
    $("picker-count").textContent = state.browse.path || "";
    $("add-selected").textContent = state.picker.wantsName ? "Save here" : "Use this folder";
    $("add-selected").disabled = !state.browse.path || !named;
    return;
  }
  const n = state.browse.chosen.size;
  const relinking = state.browse.relink >= 0;
  $("picker-count").textContent = n
    ? (relinking ? [...state.browse.chosen][0].split(/[\\/]/).pop() : `${n} selected`)
    : "Nothing selected";
  $("add-selected").disabled = !n;
  $("add-selected").textContent = relinking ? "Relink" : "Add selected";
}

$("picker-name").addEventListener("input", updatePickerCount);

/** The folder a clip was last pointed at, so relinking starts somewhere useful. */
function folderOf(path) {
  const cut = String(path).replace(/[\\/][^\\/]*$/, "");
  return cut === String(path) ? "" : cut;
}

const baseName = (path) => String(path).split(/[\\/]/).pop();

/**
 * Other missing clips whose file sits in the folder now on screen. Moving a
 * drive breaks every path at once, so re-pointing one should be able to carry
 * the rest with it.
 */
function relinkable() {
  if (state.browse.relink < 0) return [];
  const here = new Map(state.browse.files.map((f) => [baseName(f.path).toLowerCase(), f.path]));
  return state.project.clips
    .map((clip, index) => ({ clip, index }))
    .filter(({ clip, index }) =>
      index !== state.browse.relink && clip.missing &&
      here.has(baseName(clip.path).toLowerCase())
    )
    .map(({ clip, index }) => ({ index, path: here.get(baseName(clip.path).toLowerCase()) }));
}

function showRelinkRest() {
  const others = relinkable();
  const row = $("relink-rest");
  row.classList.toggle("hidden", !others.length);
  $("relink-rest-text").textContent =
    `Also relink ${others.length} other missing clip(s) found in this folder`;
}

/**
 * Ask for a folder, optionally with a file name. Resolves to
 * `{folder, name}` or null. Used for the output folder and Save as, so those
 * do not each grow their own half of a file dialog.
 */
function choosePath({ title, subtitle, start, filename, extension } = {}) {
  return new Promise((resolve) => {
    state.browse.relink = -1;
    state.browse.chosen.clear();
    state.picker = { resolve, wantsName: filename !== undefined };

    $("picker-title").textContent = title || "Choose a folder";
    $("picker-sub").classList.toggle("hidden", !subtitle);
    if (subtitle) $("picker-sub").textContent = subtitle;
    $("relink-rest").classList.add("hidden");
    $("picker-name-row").classList.toggle("hidden", filename === undefined);
    $("picker-name").value = filename || "";
    $("picker-ext").textContent = extension || "";

    $("picker").classList.remove("hidden");
    browse(start || state.browse.path || "");
    updatePickerCount();
    (filename === undefined ? $("browse-path") : $("picker-name")).focus();
  });
}

function finishPicker(result) {
  const pending = state.picker;
  state.picker = null;
  $("picker-name-row").classList.add("hidden");
  closePicker();
  if (pending) pending.resolve(result);
}

function openPicker(relinkIndex) {
  const clip = relinkIndex >= 0 ? state.project.clips[relinkIndex] : null;
  state.picker = null;
  state.browse.relink = clip ? relinkIndex : -1;
  state.browse.chosen.clear();
  $("picker-name-row").classList.add("hidden");

  $("picker-title").textContent = clip ? "Relink clip" : "Add files";
  $("picker-sub").classList.toggle("hidden", !clip);
  if (clip) $("picker-sub").textContent = `${clip.label} — was ${clip.path}`;
  $("relink-rest-box").checked = true;
  $("relink-rest").classList.add("hidden");

  $("picker").classList.remove("hidden");
  const start = clip ? folderOf(clip.path) : state.browse.path;
  if (clip || !state.browse.loaded) {
    state.browse.loaded = true;
    browse(start || "");
  } else {
    showRelinkRest();
  }
  updatePickerCount();
  $("browse-path").focus();
}

function closePicker() {
  $("picker").classList.add("hidden");
  state.browse.relink = -1;
  // A dismissed request must still settle, or its caller waits for ever.
  if (state.picker) finishPicker(null);
}

/** Point a clip at a new file, keeping its label, regions and joins. */
async function relinkClip(index, path) {
  const clip = state.project.clips[index];
  clip.path = path;
  clip.missing = false;
  delete state.probes[path];
  const info = await probe(path);

  // A shorter replacement cannot keep regions that ran past its end.
  const duration = info.duration || 0;
  if (duration && clip.regions) {
    clip.regions = clip.regions
      .map((r) => ({ ...r, start: Math.min(r.start, duration), end: Math.min(r.end, duration) }))
      .filter((r) => r.end - r.start > MIN_REGION / 2);
  }
  return info;
}

$("open-picker").addEventListener("click", () => openPicker(-1));
$("close-picker").addEventListener("click", closePicker);
$("picker").addEventListener("click", (e) => {
  if (e.target === $("picker")) closePicker();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("picker").classList.contains("hidden")) closePicker();
});

$("browse-go").addEventListener("click", () => browse($("browse-path").value));
$("browse-path").addEventListener("keydown", (e) => {
  if (e.key === "Enter") browse($("browse-path").value);
});

$("add-selected").addEventListener("click", async () => {
  if (state.picker) {
    finishPicker({ folder: state.browse.path, name: $("picker-name").value.trim() });
    return;
  }
  if (state.browse.relink >= 0) {
    const target = [...state.browse.chosen][0];
    if (!target) return toast("Pick the file to relink to.", true);
    const index = state.browse.relink;
    const others = $("relink-rest-box").checked ? relinkable() : [];
    await relinkClip(index, target);
    for (const other of others) await relinkClip(other.index, other.path);
    closePicker();
    refreshAll();
    if (state.selected === index) selectClip(index);
    toast(others.length ? `Relinked ${others.length + 1} clips.` : "Relinked.");
    return;
  }

  const picked = state.browse.files.filter((f) => state.browse.chosen.has(f.path));
  if (!picked.length) {
    toast("Nothing selected.");
    return;
  }
  const d = state.project.defaults;
  picked.forEach((file) => {
    state.project.clips.push({
      path: file.path,
      label: file.name.replace(/\.[^.]+$/, ""),
      join: d.join,
      join_duration: d.join === "cut" ? 0 : d.join === "fade" ? d.fade : d.crossfade,
      trim_silence: d.trim_silence,
      audio_gain_db: 0,
      balance_db: null,
      audio_overlap: d.audio_overlap === undefined ? null : d.audio_overlap,
      audio_lead: d.audio_lead || 0,
      regions: [],
      missing: false,
    });
  });
  state.browse.chosen.clear();
  document.querySelectorAll("#browse-list input[type=checkbox]").forEach((b) => (b.checked = false));
  updatePickerCount();
  if (state.selected < 0) state.selected = 0;
  refreshAll();
  closePicker();
  toast(`Added ${picked.length} clip(s).`);
  // Levelling is on, so the clips just added should be levelled too -- that is
  // the point of it being a project setting rather than a one-off button.
  if (state.project.balance.enabled) runBalance({ onlyUnmeasured: true });
});

$("clips-to-edit").addEventListener("click", () => showPage("edit"));

// ---------------------------------------------------------------- shared rendering

function miniTimeline(target) {
  target.innerHTML = "";
  if (!state.project.clips.length) {
    target.appendChild(el("div", "empty-note", "No clips yet."));
    return;
  }
  state.project.clips.forEach((clip, i) => {
    const row = el("div", "mini-row" + (clip.missing ? " missing" : ""));
    row.appendChild(el("div", "idx", String(i + 1)));
    row.appendChild(el("div", "name", clip.label));
    const tags = [];
    if (i > 0) tags.push(joinLabel(clip));
    if (clip.trim_silence) tags.push("trim silence");
    if (clip.audio_gain_db) tags.push(`${clip.audio_gain_db > 0 ? "+" : ""}${clip.audio_gain_db} dB`);
    if (balanceDb(clip)) tags.push(`levelled ${balanceDb(clip) > 0 ? "+" : ""}${balanceDb(clip)}`);
    if (i > 0 && !audioFollowsPicture(clip)) tags.push(audioJoinLabel(clip));
    if (clip.regions && clip.regions.length) tags.push(`${clip.regions.length} region(s)`);
    if (clip.missing) tags.push("MISSING");
    tags.push(fmt(keptDuration(clip)));
    row.appendChild(el("div", "tag", tags.join("  ·  ")));
    target.appendChild(row);
  });
}

/** Wire a row so it can be dragged to a new position in the timeline. */
function makeReorderable(node, index, onDone) {
  node.draggable = true;
  node.addEventListener("dragstart", (e) => {
    node.classList.add("dragging");
    e.dataTransfer.setData("text/plain", String(index));
    e.dataTransfer.effectAllowed = "move";
  });
  node.addEventListener("dragend", () => {
    node.classList.remove("dragging");
    document.querySelectorAll(".drop-target").forEach((n) => n.classList.remove("drop-target"));
  });
  node.addEventListener("dragover", (e) => {
    e.preventDefault();
    node.classList.add("drop-target");
  });
  node.addEventListener("dragleave", () => node.classList.remove("drop-target"));
  node.addEventListener("drop", (e) => {
    e.preventDefault();
    node.classList.remove("drop-target");
    const from = parseInt(e.dataTransfer.getData("text/plain"), 10);
    if (isNaN(from) || from === index) return;
    const [moved] = state.project.clips.splice(from, 1);
    state.project.clips.splice(index, 0, moved);
    state.selected = index;
    onDone();
  });
}

/** Remove a clip, keeping it for one undo -- regions are slow to rebuild. */
function removeClip(index) {
  const clip = state.project.clips[index];
  if (!clip) return;
  state.project.clips.splice(index, 1);
  state.removed = { clip, index };
  if (state.selected >= state.project.clips.length) {
    state.selected = state.project.clips.length - 1;
  }
  refreshAll();
  selectClip(state.selected);
  toast(`Removed ${clip.label} — Ctrl+Z to put it back.`);
}

function undoRemove() {
  const last = state.removed;
  if (!last) return false;
  state.removed = null;
  const at = Math.min(last.index, state.project.clips.length);
  state.project.clips.splice(at, 0, last.clip);
  refreshAll();
  selectClip(at);
  toast(`${last.clip.label} is back.`);
  return true;
}

document.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
  if (undoRemove()) event.preventDefault();
});

function refreshAll() {
  $("setup-count").textContent = state.project.clips.length;
  renderClipTable();
  renderClipList();
  $("project-title").textContent = state.project.title || "";
}

// ---------------------------------------------------------------- clips page

function renderClipTable() {
  const table = $("clip-table");
  table.innerHTML = "";

  if (!state.project.clips.length) {
    table.appendChild(el("div", "empty-note", "No clips yet — add some files above."));
    $("clips-total").textContent = "";
    return;
  }

  const missing = state.project.clips.filter((c) => c.missing).length;
  if (missing) {
    const banner = el("div", "clip-missing-note");
    banner.textContent =
      `${missing} clip(s) point at files that are not there. Relink one and the ` +
      "others in the same folder can follow.";
    table.appendChild(banner);
  }

  const head = el("div", "clip-head");
  ["", "#", "Clip", "Joins from previous", "Audio", "Kept", ""].forEach((title) =>
    head.appendChild(el("div", null, title))
  );
  table.appendChild(head);

  state.project.clips.forEach((clip, i) => {
    const row = el("div", "clip-row" + (clip.missing ? " missing" : "") +
      (i === state.selected ? " selected" : ""));

    row.appendChild(el("div", "grip", "≡"));
    row.appendChild(el("div", "idx", String(i + 1)));

    const name = el("div", "who");
    name.appendChild(el("div", "title", clip.label));
    name.appendChild(el("div", "path", clip.path));
    name.addEventListener("click", () => {
      selectClip(i);
      showPage("edit");
    });
    row.appendChild(name);

    const join = el("div", "join-cell");
    if (i === 0) {
      join.appendChild(el("span", "hint", "first clip"));
    } else {
      const pick = el("select");
      ["crossfade", "cut", "fade"].forEach((value) => {
        const option = el("option", null, value);
        option.value = value;
        pick.appendChild(option);
      });
      pick.value = clip.join;
      pick.addEventListener("change", () => {
        clip.join = pick.value;
        const d = state.project.defaults;
        clip.join_duration = clip.join === "cut" ? 0 : clip.join === "fade" ? d.fade : d.crossfade;
        refreshAll();
      });
      join.appendChild(pick);

      const secs = el("input", "mono jd");
      secs.type = "number";
      secs.step = "0.1";
      secs.min = "0";
      secs.value = clip.join_duration;
      secs.disabled = clip.join === "cut";
      secs.addEventListener("change", () => {
        clip.join_duration = Math.max(0, parseFloat(secs.value) || 0);
        refreshAll();
      });
      join.appendChild(secs);
    }
    row.appendChild(join);

    const audio = el("div", "audio-cell");
    const gain = el("input", "mono jd");
    gain.type = "number";
    gain.step = "0.5";
    gain.title = "Volume trim for this clip, in dB";
    gain.value = clip.audio_gain_db || 0;
    gain.addEventListener("change", () => {
      clip.audio_gain_db = parseFloat(gain.value) || 0;
      if (i === state.selected) syncGainControls();
      refreshAll();
    });
    audio.appendChild(gain);
    audio.appendChild(el("span", "unit", "dB"));

    // What levelling worked out, so the Clips page reflects it as soon as it runs.
    const levelled = balanceDb(clip);
    if (levelled) {
      const tag = el("span", "levelled", `${levelled > 0 ? "+" : ""}${levelled}`);
      tag.title = "Set by Balance clip levels; adds to the trim on the left";
      audio.appendChild(tag);
    }

    const trim = el("label", "check");
    const box = el("input");
    box.type = "checkbox";
    box.checked = !!clip.trim_silence;
    box.addEventListener("change", () => {
      clip.trim_silence = box.checked;
      refreshAll();
    });
    trim.appendChild(box);
    trim.appendChild(el("span", null, "trim"));
    audio.appendChild(trim);
    row.appendChild(audio);

    const kept = el("div", "kept");
    kept.appendChild(el("div", null, fmt(keptDuration(clip))));
    const notes = [];
    if (clip.regions && clip.regions.length) notes.push(`${clip.regions.length} region(s)`);
    if (clip.missing) notes.push("missing");
    kept.appendChild(el("div", "hint", notes.join(" · ")));
    row.appendChild(kept);

    const actions = el("div", "actions");
    const relink = el("button", "ghost small" + (clip.missing ? " urgent" : ""), "Relink");
    relink.title = "Point this clip at a different file";
    relink.addEventListener("click", (event) => {
      event.stopPropagation();
      openPicker(i);
    });
    actions.appendChild(relink);

    const remove = el("button", "remove", "×");
    remove.title = "Remove clip";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      removeClip(i);
    });
    actions.appendChild(remove);
    row.appendChild(actions);

    makeReorderable(row, i, () => refreshAll());
    table.appendChild(row);
  });

  $("clips-total").textContent =
    `${state.project.clips.length} clips · ${estimateLabel()}`;
}

// ---------------------------------------------------------------- edit: clip rail

function renderClipList() {
  const list = $("clip-list");
  list.innerHTML = "";

  state.project.clips.forEach((clip, i) => {
    const item = el("li", "clip-item" + (i === state.selected ? " selected" : "") + (clip.missing ? " missing" : ""));
    item.dataset.index = i;

    item.appendChild(el("span", "grip", "≡"));

    const body = el("div", "body");
    body.appendChild(el("div", "title", `${i + 1}. ${clip.label}`));
    const bits = [];
    if (i > 0) bits.push(joinLabel(clip));
    bits.push(fmt(keptDuration(clip)));
    if (clip.regions && clip.regions.length) bits.push(`${clip.regions.length} region(s)`);
    if (clip.trim_silence) bits.push("silence");
    if (clip.audio_gain_db) bits.push(`${clip.audio_gain_db > 0 ? "+" : ""}${clip.audio_gain_db}dB`);
    if (balanceDb(clip)) bits.push(`lvl ${balanceDb(clip) > 0 ? "+" : ""}${balanceDb(clip)}`);
    if (i > 0 && !audioFollowsPicture(clip)) bits.push(audioJoinLabel(clip));
    if (clip.missing) bits.push("missing");
    body.appendChild(el("div", "sub", bits.join(" · ")));
    item.appendChild(body);

    const remove = el("button", "remove", "×");
    remove.title = "Remove clip";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      removeClip(i);
    });
    item.appendChild(remove);

    item.addEventListener("click", () => selectClip(i));
    makeReorderable(item, i, () => {
      refreshAll();
      selectClip(state.selected);
    });

    list.appendChild(item);
  });

  $("rail-total").textContent = state.project.clips.length
    ? `${state.project.clips.length} clips · ${estimateLabel()}`
    : "No clips yet — add some on the Clips page.";

  if (state.selected < 0 || !state.project.clips.length) {
    $("clip-editor").classList.add("hidden");
    $("no-clip").classList.remove("hidden");
  }
}

// ---------------------------------------------------------------- edit: preview audio
//
// The preview runs through a gain node so the dB trim you dial in is the level
// you hear. Without Web Audio a boost cannot be previewed, only a cut.

const video = $("preview");
let audioGraph = null;

function ensureAudioGraph() {
  if (audioGraph !== null) return audioGraph;
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) return (audioGraph = false);
  try {
    const context = new Context();
    const source = context.createMediaElementSource(video);
    const gain = context.createGain();
    source.connect(gain).connect(context.destination);
    audioGraph = { context, gain };
  } catch {
    audioGraph = false;
  }
  return audioGraph;
}

/** The levelling trim, only when levelling is switched on. */
function balanceDb(clip) {
  if (!clip || !state.project.balance.enabled) return 0;
  return clip.balance_db || 0;
}

function totalGainDb() {
  const clip = state.project.clips[state.selected];
  return ((clip && clip.audio_gain_db) || 0) + balanceDb(clip);
}

function applyPreviewGain() {
  const db = totalGainDb();
  const ratio = Math.pow(10, db / 20);
  const graph = ensureAudioGraph();
  if (graph) {
    // Ramp rather than jump, so dragging the slider does not click.
    const now = graph.context.currentTime;
    graph.gain.gain.cancelScheduledValues(now);
    graph.gain.gain.setTargetAtTime($("clip-mute").checked ? 0 : ratio, now, 0.02);
  } else {
    video.volume = $("clip-mute").checked ? 0 : Math.min(1, ratio);
  }
}

function showGainTotal() {
  const db = totalGainDb();
  const levelled = balanceDb(state.project.clips[state.selected]);
  const graph = ensureAudioGraph();
  $("clip-gain-total").textContent =
    `${db > 0 ? "+" : ""}${db.toFixed(1)} dB total` +
    (levelled ? ` (${levelled > 0 ? "+" : ""}${levelled} from levelling)` : "") +
    (!graph && db > 0 ? "  (preview cannot boost)" : "");
}

// ---------------------------------------------------------------- join audio
//
// An overlap is paid for out of source either side of the cut: the outgoing
// clip plays on past its out point, the incoming one starts before its in
// point. A clip used to its last frame has nothing to give, so the same
// arithmetic the renderer uses is mirrored here to say so before you render.

function clipAudioBlend(clip) {
  return clip.audio_overlap === null || clip.audio_overlap === undefined
    ? clip.join_duration || 0
    : clip.audio_overlap;
}

/** Seconds of source sitting outside a clip's own in and out points. */
function handles(clip) {
  const info = state.probes[clip.path];
  const duration = (info && info.duration) || 0;
  const regions = clip.regions || [];
  if (!regions.length) return { head: 0, tail: 0 };
  return {
    head: Math.max(0, regions[0].start),
    tail: Math.max(0, duration - regions[regions.length - 1].end),
  };
}

/** What the renderer will actually be able to give this join. */
function overlapReport(index) {
  const clip = state.project.clips[index];
  const before = state.project.clips[index - 1];
  if (!clip || !before) return null;

  const span = clip.join_duration || 0;
  const half = (clipAudioBlend(clip) - span) / 2;
  const lead = clip.audio_lead || 0;
  const wantHead = lead + half;
  const wantTail = half - lead;
  if (Math.abs(wantHead) < 1e-9 && Math.abs(wantTail) < 1e-9) return null;

  const room = handles(clip).head;
  const roomBefore = handles(before).tail;
  const head = Math.min(Math.max(wantHead, -0.45 * keptDuration(clip)), room);
  const tail = Math.min(Math.max(wantTail, -0.45 * keptDuration(before)), roomBefore);
  return {
    asked: span + wantHead + wantTail,
    granted: Math.max(0, span + head + tail),
    // Silence trimming only shortens clips, so it can only free up more.
    estimate: clip.trim_silence || before.trim_silence,
  };
}

function syncJoinAudio() {
  const index = state.selected;
  const clip = state.project.clips[index];
  const bar = $("join-audio-bar");
  if (!clip || index < 1) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  $("clip-audio-overlap").value =
    clip.audio_overlap === null || clip.audio_overlap === undefined ? "" : clip.audio_overlap;
  $("clip-audio-overlap").placeholder = `follow ${clip.join_duration || 0}`;
  $("clip-audio-lead").value = clip.audio_lead || 0;

  const note = $("clip-audio-note");
  const report = overlapReport(index);
  if (!report) {
    note.textContent = "Sound changes with the picture.";
    note.className = "hint";
  } else if (report.granted + 0.01 < report.asked) {
    note.textContent =
      `Only ${report.granted.toFixed(2)}s of ${report.asked.toFixed(2)}s available — ` +
      "trim a clip's picture back to leave the sound something to play.";
    note.className = "hint warn-text";
  } else {
    note.textContent =
      `${report.granted.toFixed(2)}s overlap` +
      (report.estimate ? " (before silence trimming)" : "");
    note.className = "hint ok-text";
  }
}

function setJoinAudio() {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  const raw = $("clip-audio-overlap").value.trim();
  clip.audio_overlap = raw === "" ? null : Math.max(0, parseFloat(raw) || 0);
  clip.audio_lead = parseFloat($("clip-audio-lead").value) || 0;
  syncJoinAudio();
  refreshAll();
}

$("clip-audio-overlap").addEventListener("change", setJoinAudio);
$("clip-audio-lead").addEventListener("change", setJoinAudio);

function syncGainControls() {
  const clip = state.project.clips[state.selected];
  const db = (clip && clip.audio_gain_db) || 0;
  $("clip-gain").value = db;
  $("clip-gain-slider").value = clamp(db, -24, 24);
  showGainTotal();
  applyPreviewGain();
}

function setClipGain(db) {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  clip.audio_gain_db = Math.round(clamp(db, -60, 60) * 10) / 10;
  syncGainControls();
  renderClipTable();
  renderClipList();
}

$("clip-gain").addEventListener("change", () => setClipGain(parseFloat($("clip-gain").value) || 0));
$("clip-gain-slider").addEventListener("input", () =>
  setClipGain(parseFloat($("clip-gain-slider").value) || 0)
);
$("clip-mute").addEventListener("change", applyPreviewGain);

// ---------------------------------------------------------------- edit: clip editor

// Asking the element what it can decode beats any list: Chromium decodes HEVC
// only through the platform's hardware decoder, so the honest answer differs
// between machines, and between a GPU-accelerated window and a software one.
const CODEC_PROBE = {
  h264: 'video/mp4; codecs="avc1.640028"',
  hevc: 'video/mp4; codecs="hvc1.1.6.L93.B0"',
  h265: 'video/mp4; codecs="hvc1.1.6.L93.B0"',
  av1: 'video/mp4; codecs="av01.0.05M.08"',
  vp9: 'video/webm; codecs="vp09.00.10.08"',
  vp8: 'video/webm; codecs="vp8"',
};

/** true, false, or null when we have no way to ask about this codec. */
function codecSupported(vcodec) {
  const type = CODEC_PROBE[String(vcodec || "").toLowerCase()];
  if (!type) return null;
  if (document.createElement("video").canPlayType(type)) return true;
  if (window.MediaSource && MediaSource.isTypeSupported(type)) return true;
  return false;
}

/** Why this clip cannot be previewed here, or "" when it can. */
function previewProblem(info) {
  if (!info) return "";
  if (info.preview_note) return info.preview_note;
  if (codecSupported(info.vcodec) === false) {
    return `This window cannot decode ${String(info.vcodec).toUpperCase()} video ` +
      "— it needs hardware support that is not available here.";
  }
  return "";
}

const STILL_EDITABLE =
  "Regions can still be set from the filmstrip and the time fields below — " +
  "only playback is missing. The render is not affected.";

function showFallback(message) {
  // Hide first: clearing src fires its own error event, which we must ignore.
  video.classList.add("hidden");
  try { video.pause(); } catch { /* nothing loaded */ }
  video.removeAttribute("src");
  video.load();
  const node = $("video-fallback");
  node.classList.remove("hidden");
  node.textContent = message;
}

function showVideo(clip) {
  video.classList.remove("hidden");
  $("video-fallback").classList.add("hidden");
  video.src = mediaUrl(clip.path);
  video.load();
}

/**
 * Some files load cleanly and then decode nothing -- HEVC in an .mp4 reports a
 * successful load, `readyState` 4 and no error, but a 0x0 frame. Guessing
 * playability from the file extension cannot catch that, and the symptom is a
 * black rectangle with no explanation, so check what actually arrived.
 */
function checkPreviewDecoded() {
  const clip = state.project.clips[state.selected];
  if (!clip || video.classList.contains("hidden")) return;
  if (video.videoWidth > 0 && video.videoHeight > 0) return;
  const codec = ((state.probes[clip.path] || {}).vcodec || "").toUpperCase();
  showFallback(
    (codec ? `${codec} video ` : "This file ") +
    "loaded but decoded no picture in this window. " + STILL_EDITABLE
  );
}

async function selectClip(index) {
  state.selected = index;
  state.activeRegion = -1;
  setMarking(false);
  stopPlayback();

  const clip = state.project.clips[index];
  if (!clip) {
    $("clip-editor").classList.add("hidden");
    $("no-clip").classList.remove("hidden");
    renderClipList();
    return;
  }

  $("no-clip").classList.add("hidden");
  $("clip-editor").classList.remove("hidden");
  $("clip-name").textContent = clip.label;
  $("clip-join").value = clip.join;
  $("clip-join-duration").value = clip.join_duration;
  $("clip-join-duration").disabled = clip.join === "cut";
  $("clip-trim").checked = clip.trim_silence;

  const info = await probe(clip.path);
  const bits = [clip.path];
  if (info.duration) bits.push(fmt(info.duration));
  if (info.width) bits.push(`${info.width}x${info.height} @ ${info.fps}fps`);
  if (info.has_audio === false) bits.push("no audio");
  $("clip-meta").textContent = bits.join("  ·  ");
  $("clip-meta").title = clip.path;

  const problem = previewProblem(info);
  if (clip.missing) {
    showFallback("File not found. Use Relink on the Clips page to point it at the file.");
  } else if (problem) {
    showFallback(problem + " " + STILL_EDITABLE);
  } else {
    showVideo(clip);
  }

  lastTarget = null;
  $("time-readout").textContent = "0:00 / 0:00";
  resetView();
  syncGainControls();
  syncJoinAudio();
  drawRegions();
  renderClipList();
  renderClipTable();
}

$("clip-join").addEventListener("change", () => {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  clip.join = $("clip-join").value;
  const d = state.project.defaults;
  clip.join_duration = clip.join === "cut" ? 0 : clip.join === "fade" ? d.fade : d.crossfade;
  $("clip-join-duration").value = clip.join_duration;
  $("clip-join-duration").disabled = clip.join === "cut";
  syncJoinAudio();
  refreshAll();
});

$("clip-join-duration").addEventListener("change", () => {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  clip.join_duration = parseFloat($("clip-join-duration").value) || 0;
  syncJoinAudio();
  refreshAll();
});

$("clip-remove").addEventListener("click", () => {
  if (state.selected >= 0) removeClip(state.selected);
});

$("clip-trim").addEventListener("change", () => {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  clip.trim_silence = $("clip-trim").checked;
  refreshAll();
});

function currentDuration() {
  const clip = state.project.clips[state.selected];
  const info = clip ? state.probes[clip.path] : null;
  return (info && info.duration) || video.duration || 0;
}

// ---------------------------------------------------------------- edit: zoom
//
// Everything drawn on the scrubber -- filmstrip, regions, ruler, playhead --
// is positioned against `view`, the window of the clip currently on screen.
// The overview strip below shows where that window sits in the whole clip.

function view() {
  const duration = currentDuration();
  if (!state.view || !duration) return { start: 0, end: duration, span: duration };
  const start = clamp(state.view.start, 0, Math.max(0, duration - MIN_SPAN));
  const end = clamp(state.view.end, start + Math.min(MIN_SPAN, duration), duration);
  return { start, end, span: end - start };
}

function maxZoom() {
  const duration = currentDuration();
  return duration > MIN_SPAN ? duration / MIN_SPAN : 1;
}

function resetView() {
  state.view = null;
  refreshScrubber();
}

/** Show `span` seconds, holding `anchor` at the same place on screen. */
function setView(span, anchor, anchorRatio) {
  const duration = currentDuration();
  if (!duration) return;
  span = clamp(span, Math.min(MIN_SPAN, duration), duration);
  let start = clamp(anchor - span * anchorRatio, 0, duration - span);
  state.view = span >= duration - 1e-6 ? null : { start, end: start + span };
  refreshScrubber();
}

function zoomBy(factor, anchor, anchorRatio) {
  const v = view();
  setView(v.span / factor, anchor === undefined ? v.start + v.span / 2 : anchor,
          anchorRatio === undefined ? 0.5 : anchorRatio);
}

function panTo(start) {
  const duration = currentDuration();
  const v = view();
  if (v.span >= duration) return;
  state.view = { start: clamp(start, 0, duration - v.span) };
  state.view.end = state.view.start + v.span;
  refreshScrubber();
}

/** Keep the playhead on screen while playing a zoomed-in clip. */
function followPlayhead(at) {
  const v = view();
  const duration = currentDuration();
  if (v.span >= duration) return;
  if (at < v.start || at > v.end) panTo(at - v.span * 0.25);
}

function refreshScrubber() {
  drawRuler();
  drawRegions();
  drawOverview();
  scheduleFilmstrip();
  syncZoomControls();
  updatePlayhead(playheadTime());
}

function syncZoomControls() {
  const duration = currentDuration();
  const v = view();
  const zoom = v.span > 0 ? duration / v.span : 1;
  const top = maxZoom();
  const slider = $("zoom-slider");
  slider.value = top > 1 ? Math.round((Math.log(zoom) / Math.log(top)) * 100) : 0;
  $("zoom-readout").textContent = duration
    ? `${zoom.toFixed(zoom < 10 ? 1 : 0)}×   ${fmtTick(v.start, v.span)} – ${fmtTick(v.end, v.span)}`
    : "";
  $("zoom-out").disabled = zoom <= 1.0001;
  $("zoom-in").disabled = zoom >= top - 1e-6;
  $("zoom-reset").disabled = zoom <= 1.0001;
}

$("zoom-slider").addEventListener("input", () => {
  const duration = currentDuration();
  if (!duration) return;
  const top = maxZoom();
  const zoom = Math.pow(top, parseFloat($("zoom-slider").value) / 100);
  const v = view();
  setView(duration / zoom, playheadInView() ? playheadTime() : v.start + v.span / 2, 0.5);
});

// The wheel over the slider nudges it, matching the wheel over the scrubber.
$("zoom-slider").addEventListener("wheel", (event) => {
  event.preventDefault();
  zoomBy(event.deltaY < 0 ? 1.25 : 1 / 1.25);
}, { passive: false });

$("zoom-in").addEventListener("click", () => zoomBy(2));
$("zoom-out").addEventListener("click", () => zoomBy(0.5));
$("zoom-reset").addEventListener("click", resetView);

// Rolling over the timeline zooms around the pointer, so the frame under the
// cursor stays put -- shift rolls sideways instead.
$("scrubber").addEventListener("wheel", (event) => {
  if (!currentDuration()) return;
  event.preventDefault();
  const box = $("scrubber").getBoundingClientRect();
  const ratio = clamp((event.clientX - box.left) / box.width, 0, 1);
  const v = view();
  if (event.shiftKey) {
    panTo(v.start + v.span * 0.2 * Math.sign(event.deltaY));
  } else {
    setView(v.span / (event.deltaY < 0 ? 1.25 : 1 / 1.25), v.start + v.span * ratio, ratio);
  }
}, { passive: false });

// -- the overview strip: pan by dragging the window, zoom by dragging an edge

function drawOverview() {
  const duration = currentDuration();
  const v = view();
  const window = $("overview-window");
  const pct = (t) => (duration ? (t / duration) * 100 : 0);

  window.style.left = pct(v.start) + "%";
  window.style.width = Math.max(0.6, pct(v.span)) + "%";
  $("overview").classList.toggle("zoomed", v.span < duration - 1e-6);
  $("overview-playhead").style.left = pct(playheadTime()) + "%";

  const layer = $("overview-regions");
  layer.innerHTML = "";
  const clip = state.project.clips[state.selected];
  (clip ? clip.regions || [] : []).forEach((region) => {
    const band = el("div", "ov-region");
    band.style.left = pct(region.start) + "%";
    band.style.width = Math.max(0.3, pct(region.end - region.start)) + "%";
    layer.appendChild(band);
  });
}

$("overview").addEventListener("pointerdown", (event) => {
  const duration = currentDuration();
  if (!duration) return;
  event.preventDefault();
  $("overview").setPointerCapture(event.pointerId);

  const box = $("overview").getBoundingClientRect();
  const at = (clientX) => clamp((clientX - box.left) / box.width, 0, 1) * duration;
  const v = view();
  const handle = event.target.closest(".ov-handle");

  if (handle) {
    const edge = handle.classList.contains("left") ? "start" : "end";
    state.overviewDrag = { kind: "resize", edge };
  } else if (event.target.closest(".overview-window")) {
    state.overviewDrag = { kind: "pan", grab: at(event.clientX) - v.start };
  } else {
    // A click on empty track centres the window there.
    panTo(at(event.clientX) - v.span / 2);
    state.overviewDrag = { kind: "pan", grab: v.span / 2 };
  }
  state.overviewDrag.at = at;
});

$("overview").addEventListener("pointermove", (event) => {
  const drag = state.overviewDrag;
  if (!drag) return;
  const at = drag.at(event.clientX);
  const v = view();
  if (drag.kind === "pan") {
    panTo(at - drag.grab);
  } else if (drag.edge === "start") {
    const end = v.end;
    setView(Math.max(MIN_SPAN, end - at), Math.min(at, end - MIN_SPAN), 0);
  } else {
    setView(Math.max(MIN_SPAN, at - v.start), v.start, 0);
  }
});

$("overview").addEventListener("pointerup", (event) => {
  if (!state.overviewDrag) return;
  state.overviewDrag = null;
  $("overview").releasePointerCapture(event.pointerId);
});

// ---------------------------------------------------------------- edit: filmstrip & ruler

let filmstripTimer = null;
let filmstripKey = "";

function scheduleFilmstrip() {
  clearTimeout(filmstripTimer);
  filmstripTimer = setTimeout(buildFilmstrip, 140);
}

function buildFilmstrip() {
  const clip = state.project.clips[state.selected];
  const strip = $("filmstrip");
  const duration = currentDuration();
  if (!clip || !duration || clip.missing) {
    strip.innerHTML = "";
    filmstripKey = "";
    return;
  }
  const v = view();
  const key = `${clip.path}|${v.start.toFixed(2)}|${v.span.toFixed(2)}`;
  if (key === filmstripKey) return;
  filmstripKey = key;

  strip.innerHTML = "";
  const count = 12;
  for (let i = 0; i < count; i++) {
    const at = v.start + (v.span * (i + 0.5)) / count;
    const img = new Image();
    img.loading = "lazy";
    img.draggable = false;
    img.src = `/thumb?path=${encodeURIComponent(clip.path)}&t=${at.toFixed(2)}`;
    img.onerror = () => img.remove();
    strip.appendChild(img);
  }
}

function drawRuler() {
  const ruler = $("ruler");
  ruler.innerHTML = "";
  const v = view();
  for (let i = 0; i <= 4; i++) {
    ruler.appendChild(el("span", null, fmtTick(v.start + (v.span * i) / 4, v.span)));
  }
}

// ---------------------------------------------------------------- edit: regions

function drawRegions() {
  const clip = state.project.clips[state.selected];
  const layer = $("region-layer");
  const rows = $("region-rows");
  layer.innerHTML = "";
  rows.innerHTML = "";
  if (!clip) return;

  const duration = currentDuration();
  const v = view();
  const regions = clip.regions || [];
  $("region-count").textContent = regions.length;

  regions.forEach((region, i) => {
    // Bands are positioned against the visible window; the scrubber clips the
    // overflow, so a region running off either edge draws correctly.
    if (v.span > 0 && region.end > v.start && region.start < v.end) {
      const band = el("div", "region" + (i === state.activeRegion ? " active" : ""));
      band.style.left = ((region.start - v.start) / v.span) * 100 + "%";
      band.style.width = ((region.end - region.start) / v.span) * 100 + "%";
      band.dataset.index = i;
      band.appendChild(el("div", "label", `${i + 1}  ${fmt(region.end - region.start)}`));
      band.appendChild(el("div", "handle left"));
      band.appendChild(el("div", "handle right"));
      layer.appendChild(band);
    }

    const row = el("div", "region-row" + (i === state.activeRegion ? " active" : ""));
    row.addEventListener("click", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      selectRegion(i);
    });

    row.appendChild(el("span", "rid", String(i + 1)));

    // How this region attaches to the one before it inside the same clip.
    if (i > 0) {
      const join = el("select", "join-pick");
      [["cut", "cut"], ["crossfade", "crossfade"], ["fade", "fade"]].forEach(([value, text]) => {
        const option = el("option", null, text);
        option.value = value;
        join.appendChild(option);
      });
      join.value = region.join || "cut";
      join.addEventListener("change", () => {
        region.join = join.value;
        const d = state.project.defaults;
        region.join_duration =
          region.join === "cut" ? 0 : region.join === "fade" ? d.fade : d.crossfade;
        drawRegions();
        refreshAll();
      });
      row.appendChild(join);

      const secs = el("input", "mono jd");
      secs.type = "number";
      secs.step = "0.1";
      secs.min = "0";
      secs.value = region.join_duration || 0;
      secs.disabled = (region.join || "cut") === "cut";
      secs.title = "Join length in seconds";
      secs.addEventListener("change", () => {
        region.join_duration = Math.max(0, parseFloat(secs.value) || 0);
        if (region.join_duration === 0) region.join = "cut";
        drawRegions();
        refreshAll();
      });
      row.appendChild(secs);
    } else {
      row.appendChild(el("span", "join-spacer"));
    }

    const from = el("input", "mono");
    from.type = "text";
    from.value = fmtPrecise(region.start);
    from.addEventListener("change", () => editRegion(i, "start", from.value));
    row.appendChild(from);

    row.appendChild(el("span", "arrow", "→"));

    const to = el("input", "mono");
    to.type = "text";
    to.value = fmtPrecise(region.end);
    to.addEventListener("change", () => editRegion(i, "end", to.value));
    row.appendChild(to);

    const play = el("button", "ghost", "Play");
    play.addEventListener("click", () => playRegion(region));
    row.appendChild(play);

    const zoom = el("button", "ghost", "Zoom");
    zoom.title = "Fit this region on the timeline";
    zoom.addEventListener("click", () => {
      const span = Math.max(MIN_SPAN, (region.end - region.start) * 1.4);
      setView(span, (region.start + region.end) / 2, 0.5);
    });
    row.appendChild(zoom);

    const drop = el("button", "ghost", "×");
    drop.title = "Delete region";
    drop.addEventListener("click", () => {
      clip.regions.splice(i, 1);
      if (clip.regions.length) clip.regions[0].join = "cut";
      state.activeRegion = Math.min(state.activeRegion, clip.regions.length - 1);
      drawRegions();
      drawOverview();
      refreshAll();
    });
    row.appendChild(drop);

    row.appendChild(el("span", "dur", fmt(region.end - region.start)));
    rows.appendChild(row);
  });

  syncJoinAudio();

  const hint = $("region-hint");
  hint.textContent = regions.length
    ? `Keeping ${fmt(keptDuration(clip))} of ${fmt(duration)}.`
    : `No regions — the whole clip plays (${fmt(duration)}).`;
}

function selectRegion(index) {
  state.activeRegion = index;
  drawRegions();
}

function editRegion(index, field, text) {
  const clip = state.project.clips[state.selected];
  const seconds = parseTime(text);
  if (seconds === null) {
    toast("Use a timecode like 1:23 or 83.5", true);
    drawRegions();
    return;
  }
  const duration = currentDuration();
  const region = clip.regions[index];
  const [low, high] = regionBounds(clip.regions, index, duration);

  if (field === "start") {
    region.start = Math.max(low, Math.min(seconds, region.end - MIN_REGION));
  } else {
    region.end = Math.min(high, Math.max(seconds, region.start + MIN_REGION));
  }
  drawRegions();
  drawOverview();
  refreshAll();
}

// ---------------------------------------------------------------- edit: playback
//
// Seeking is coalesced so dragging never queues work the decoder cannot keep
// up with. `fastSeek` lands on the nearest keyframe, which is what you want
// while scrubbing but leaves audio and video resolved differently -- so every
// gesture ends, and every play starts, with an exact seek instead.

let seekPending = null;
let seekExact = false;
let lastTarget = null;   // where the last gesture asked the playhead to be
let stopAt = null;
let ticking = false;

function seekTo(seconds, exact) {
  lastTarget = seconds;
  updatePlayhead(seconds);
  if (!video.src || video.classList.contains("hidden")) return;
  seekPending = seconds;
  seekExact = !!exact;
  if (video.seeking) return;
  const go = () => {
    if (seekPending === null) return;
    const target = seekPending;
    const precise = seekExact;
    seekPending = null;
    try {
      if (!precise && video.fastSeek) video.fastSeek(target);
      else video.currentTime = target;
    } catch { /* seeking before metadata is loaded */ }
  };
  requestAnimationFrame(go);
}

/** Land exactly on the last requested frame, so both streams start together. */
function settleSeek(target) {
  if (target === undefined) target = lastTarget;
  lastTarget = null;
  if (target === null || target === undefined) return;
  if (!video.src || video.classList.contains("hidden")) return;
  seekPending = null;
  try {
    video.currentTime = target;
  } catch { /* not loaded yet */ }
}

function stopPlayback() {
  stopAt = null;
  if (video.src && !video.paused) video.pause();
}

function tick() {
  if (video.paused) {
    ticking = false;
    return;
  }
  const at = video.currentTime;
  // Region playback stops on media time, not on a timer -- a wall-clock
  // timeout drifts whenever the decoder stalls.
  if (stopAt !== null && at >= stopAt - 0.005) {
    stopAt = null;
    video.pause();
    return;
  }
  if (!state.drag) {
    updatePlayhead(at);
    followPlayhead(at);
  }
  requestAnimationFrame(tick);
}

function startTicking() {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(tick);
}

function playRegion(region) {
  if (!video.src) return;
  updatePlayhead(region.start);
  settleSeek(region.start);
  stopAt = region.end;
  startPlayback();
}

function startPlayback() {
  const graph = ensureAudioGraph();
  // An AudioContext starts suspended until a user gesture resumes it.
  if (graph && graph.context.state === "suspended") graph.context.resume();
  applyPreviewGain();
  video.play().catch(() => {});
}

function playheadInView() {
  const v = view();
  const at = playheadTime();
  return at >= v.start && at <= v.end;
}

function updatePlayhead(at) {
  const v = view();
  const head = $("playhead");
  if (v.span > 0 && at >= v.start && at <= v.end) {
    head.style.display = "";
    head.style.left = ((at - v.start) / v.span) * 100 + "%";
  } else {
    head.style.display = "none";
  }
  $("overview-playhead").style.left =
    (currentDuration() ? (at / currentDuration()) * 100 : 0) + "%";
  $("time-readout").textContent = `${fmtPrecise(at)} / ${fmt(currentDuration())}`;
}

video.addEventListener("timeupdate", () => {
  if (!state.drag && video.paused) updatePlayhead(video.currentTime);
});
video.addEventListener("seeked", () => {
  if (seekPending !== null) {
    const target = seekPending;
    const precise = seekExact;
    seekPending = null;
    try {
      if (!precise && video.fastSeek) video.fastSeek(target);
      else video.currentTime = target;
    } catch { /* ignore */ }
  }
});
video.addEventListener("loadeddata", checkPreviewDecoded);
video.addEventListener("error", () => {
  const clip = state.project.clips[state.selected];
  if (!clip || video.classList.contains("hidden")) return;
  const detail = video.error && video.error.message ? ` (${video.error.message})` : "";
  showFallback(`The preview could not open this file${detail}. ` + STILL_EDITABLE);
});
video.addEventListener("loadedmetadata", () => {
  const clip = state.project.clips[state.selected];
  if (clip && state.probes[clip.path] && !state.probes[clip.path].duration) {
    state.probes[clip.path].duration = video.duration;
  }
  refreshScrubber();
  updatePlayhead(0);
});
video.addEventListener("play", () => {
  $("play-toggle").textContent = "Pause";
  startTicking();
});
video.addEventListener("pause", () => {
  $("play-toggle").textContent = "Play";
  stopAt = null;
});

$("play-toggle").addEventListener("click", () => {
  if (!video.src) return;
  if (video.paused) {
    stopAt = null;
    settleSeek();
    startPlayback();
  } else {
    video.pause();
  }
});

// ---------------------------------------------------------------- edit: scrubber gestures
//
// One pointer gesture handler covers scrubbing, creating, moving and resizing,
// so the video seeks live throughout and the filmstrip never gets dragged.

function positionToTime(clientX) {
  const box = $("scrubber").getBoundingClientRect();
  const v = view();
  const ratio = clamp((clientX - box.left) / box.width, 0, 1);
  return v.start + ratio * v.span;
}

function setMarking(on) {
  state.marking = on;
  state.markStart = null;
  $("scrubber").classList.toggle("marking", on);
  $("mark-region").classList.toggle("primary", on);
  $("mark-hint").textContent = on
    ? "Marking: drag across the timeline, or press [ and ] at the playhead. Esc to cancel."
    : "";
}

$("mark-region").addEventListener("click", () => setMarking(!state.marking));

$("scrubber").addEventListener("pointerdown", (event) => {
  const clip = state.project.clips[state.selected];
  if (!clip || !currentDuration()) return;
  event.preventDefault();
  $("scrubber").setPointerCapture(event.pointerId);
  stopPlayback();

  const at = positionToTime(event.clientX);
  const handle = event.target.closest(".handle");
  const band = event.target.closest(".region");

  if (handle && band) {
    const index = Number(band.dataset.index);
    selectRegion(index);
    state.drag = { kind: "resize", index, edge: handle.classList.contains("left") ? "start" : "end" };
  } else if (band) {
    const index = Number(band.dataset.index);
    selectRegion(index);
    const region = clip.regions[index];
    state.drag = { kind: "move", index, grab: at - region.start, span: region.end - region.start };
  } else if (state.marking) {
    clip.regions = clip.regions || [];
    clip.regions.push(newRegion(at, at + MIN_REGION));
    clip.regions.sort((a, b) => a.start - b.start);
    const index = clip.regions.findIndex((r) => r.start === at);
    state.drag = { kind: "create", index, anchor: at };
    selectRegion(index);
  } else {
    state.drag = { kind: "scrub" };
  }

  handleDrag(event);
});

$("scrubber").addEventListener("pointermove", (event) => {
  if (state.drag) handleDrag(event);
});

$("scrubber").addEventListener("pointerup", (event) => {
  if (!state.drag) return;
  const kind = state.drag.kind;
  const clip = state.project.clips[state.selected];
  state.drag = null;
  $("scrubber").releasePointerCapture(event.pointerId);

  if (kind === "create") {
    const region = clip.regions[state.activeRegion];
    // A click rather than a drag leaves a sliver; drop it instead of keeping it.
    if (region && region.end - region.start <= MIN_REGION + 1e-3) {
      clip.regions.splice(state.activeRegion, 1);
      state.activeRegion = -1;
      toast("Drag to size the region.", true);
    } else {
      setMarking(false);
      toast("Region added.");
    }
  }
  if (clip && clip.regions) clip.regions.forEach((r, i) => { if (i === 0) r.join = "cut"; });
  // The gesture is over: land on the exact frame the playhead shows.
  settleSeek();
  drawRegions();
  drawOverview();
  refreshAll();
});

function handleDrag(event) {
  const clip = state.project.clips[state.selected];
  const drag = state.drag;
  if (!clip || !drag) return;
  const duration = currentDuration();
  const at = positionToTime(event.clientX);

  if (drag.kind === "scrub") {
    seekTo(at);
    return;
  }

  const regions = clip.regions;
  const region = regions[drag.index];
  if (!region) return;
  const [low, high] = regionBounds(regions, drag.index, duration);

  if (drag.kind === "resize") {
    if (drag.edge === "start") {
      region.start = Math.max(low, Math.min(at, region.end - MIN_REGION));
      seekTo(region.start);
    } else {
      region.end = Math.min(high, Math.max(at, region.start + MIN_REGION));
      seekTo(region.end);
    }
  } else if (drag.kind === "move") {
    const span = drag.span;
    const start = Math.max(low, Math.min(at - drag.grab, high - span));
    region.start = start;
    region.end = start + span;
    seekTo(start);
  } else if (drag.kind === "create") {
    if (at >= drag.anchor) {
      region.start = Math.max(low, drag.anchor);
      region.end = Math.min(high, Math.max(at, drag.anchor + MIN_REGION));
    } else {
      region.start = Math.max(low, Math.min(at, drag.anchor - MIN_REGION));
      region.end = Math.min(high, drag.anchor);
    }
    seekTo(at);
  }

  drawRegions();
  drawOverview();
}

// -- marking a region from the playhead

function playheadTime() {
  if (video.src && !video.classList.contains("hidden")) return video.currentTime;
  return parseTime($("time-readout").textContent.split("/")[0]) || 0;
}

$("mark-in").addEventListener("click", () => {
  const clip = state.project.clips[state.selected];
  if (!clip || !currentDuration()) return;
  state.marking = true;
  state.markStart = playheadTime();
  $("scrubber").classList.add("marking");
  $("mark-region").classList.add("primary");
  $("mark-hint").textContent =
    `Region starts at ${fmtPrecise(state.markStart)} — scrub, then press ] or End. Esc to cancel.`;
});

$("mark-out").addEventListener("click", () => {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  if (state.markStart === null) {
    toast("Set the region start first.", true);
    return;
  }
  const a = state.markStart;
  const b = playheadTime();
  const [start, end] = a < b ? [a, b] : [b, a];
  if (end - start < MIN_REGION) {
    toast(`Regions must be at least ${MIN_REGION}s.`, true);
    return;
  }

  clip.regions = clip.regions || [];
  const clash = clip.regions.find((r) => start < r.end - 1e-6 && end > r.start + 1e-6);
  if (clash) {
    toast("That overlaps an existing region.", true);
    return;
  }

  clip.regions.push(newRegion(start, end));
  clip.regions.sort((x, y) => x.start - y.start);
  clip.regions[0].join = "cut";
  state.activeRegion = clip.regions.findIndex((r) => r.start === start);
  setMarking(false);
  drawRegions();
  drawOverview();
  refreshAll();
  toast("Region added.");
});

$("clear-regions").addEventListener("click", () => {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  clip.regions = [];
  state.activeRegion = -1;
  drawRegions();
  drawOverview();
  refreshAll();
});

document.addEventListener("keydown", (event) => {
  if (!$("page-edit").classList.contains("active")) return;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
  if (event.key === "[") $("mark-in").click();
  else if (event.key === "]") $("mark-out").click();
  else if (event.key === "Escape") setMarking(false);
  else if (event.key === "+" || event.key === "=") zoomBy(2, playheadTime(), 0.5);
  else if (event.key === "-" || event.key === "_") zoomBy(0.5, playheadTime(), 0.5);
  else if (event.key === "0") resetView();
  else if (event.key === "Delete" && state.activeRegion >= 0) {
    const clip = state.project.clips[state.selected];
    clip.regions.splice(state.activeRegion, 1);
    if (clip.regions.length) clip.regions[0].join = "cut";
    state.activeRegion = -1;
    drawRegions();
    drawOverview();
    refreshAll();
  } else if (event.key === "Delete" && state.selected >= 0) {
    // No region highlighted, so Delete means the clip itself; undoable.
    removeClip(state.selected);
  } else if (event.key === " ") {
    event.preventDefault();
    $("play-toggle").click();
  }
});

// ---------------------------------------------------------------- render page

function renderSummary() {
  formToProject();
  const table = $("render-summary");
  const p = state.project;
  const missing = p.clips.filter((c) => c.missing).length;

  const rows = [
    ["Title", p.title],
    ["Clips", `${p.clips.length}${missing ? ` (${missing} missing)` : ""}`],
    ["Estimated length", estimateLabel()],
    ["Format", `${p.output.resolution} @ ${p.output.fps}fps`],
    ["Encoder", p.output.encoder + (p.output.quality === null || p.output.quality === undefined
      ? "" : `  (quality ${p.output.quality})`)],
    ["Fades", `${p.defaults.fade_in}s in · ${p.defaults.fade_out}s out`],
    ["Levelling", p.balance.enabled ? `on, ${p.balance.target_lufs} LUFS` : "off"],
    ["Audio joins", (() => {
      const offset = p.clips.filter((c, i) => i > 0 && !audioFollowsPicture(c));
      return offset.length ? `${offset.length} offset from the picture` : "follow the picture";
    })()],
    ["Output", p.output.file || "(not set)"],
  ];

  table.innerHTML = "";
  rows.forEach(([key, value]) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, key));
    tr.appendChild(el("td", null, value));
    table.appendChild(tr);
  });

  miniTimeline($("render-timeline"));
  $("render-out-file").textContent = p.output.file || "(not set)";
  checkOutput($("render-out-check"));
}

$("btn-render").addEventListener("click", async () => {
  formToProject();
  if (!state.project.clips.length) return toast("Add some clips first.", true);
  if (!state.project.output.file) return toast("Set an output file.", true);
  if (state.project.clips.some((c) => c.missing))
    return toast("Some clips are missing — remove them before rendering.", true);

  state.render = { log: [], count: 0, samples: [], hasGpu: false };
  $("render-log").textContent = "";
  // A new run starts blue again, whatever the last one ended as.
  $("progress-bar").classList.remove("done", "failed");
  $("progress-bar").style.width = "0%";
  try {
    await post("/api/render", { project: state.project });
    startPolling();
  } catch (err) {
    toast(err.message, true);
  }
});

$("btn-cancel").addEventListener("click", async () => {
  try {
    await post("/api/render/cancel", {});
  } catch (err) {
    toast(err.message, true);
  }
});

$("btn-reveal").addEventListener("click", () =>
  post("/api/reveal", { path: state.project.output.file }).catch((e) => toast(e.message, true))
);

function startPolling() {
  clearInterval(state.poll);
  state.poll = setInterval(pollRender, 700);
  pollRender();
}

// -- the pipeline, so the silent minutes before encoding are accounted for

const STAGE_MARK = {
  pending: "·", running: "▸", done: "✓", failed: "✕", skipped: "–",
};

function drawStages(stages) {
  const list = $("stage-list");
  list.innerHTML = "";
  (stages || []).forEach((stage) => {
    const item = el("li", "pipeline-step " + stage.state);
    item.appendChild(el("span", "mark", STAGE_MARK[stage.state] || "·"));
    item.appendChild(el("span", "name", stage.label));
    item.appendChild(el("span", "detail", stage.detail || ""));
    item.appendChild(el("span", "secs", stage.seconds ? `${stage.seconds}s` : ""));
    list.appendChild(item);
  });
}

// -- the log, fetched incrementally so a chatty ffmpeg stays cheap to poll

function appendLog(entries) {
  if (!entries.length) return;
  state.render.log.push(...entries);
  if (state.render.log.length > 4000) {
    state.render.log.splice(0, state.render.log.length - 4000);
  }
  drawLog();
}

function drawLog() {
  const node = $("render-log");
  const showFfmpeg = $("log-ffmpeg").checked;
  const lines = state.render.log.filter((e) => showFfmpeg || e.src !== "ffmpeg");
  node.innerHTML = "";
  lines.forEach((entry) => {
    const line = el("div", "line " + entry.src);
    line.appendChild(el("span", "stamp", fmt(entry.t)));
    line.appendChild(el("span", "text", entry.text));
    node.appendChild(line);
  });
  node.scrollTop = node.scrollHeight;
  $("log-count").textContent = `${lines.length} line(s)`;
}

$("log-ffmpeg").addEventListener("change", drawLog);

// -- the utilisation graph: a small, secondary read on where the time goes

function drawUtil() {
  const canvas = $("util-graph");
  const context = canvas.getContext("2d");
  const { width, height } = canvas;
  const samples = state.render.samples;
  context.clearRect(0, 0, width, height);

  const style = getComputedStyle(document.body);
  const line = style.getPropertyValue("--line").trim() || "#303643";
  const accent = style.getPropertyValue("--accent").trim() || "#5aa9ff";
  const good = style.getPropertyValue("--good").trim() || "#4ec98a";

  context.strokeStyle = line;
  context.lineWidth = 1;
  [0, 0.5, 1].forEach((f) => {
    const y = Math.round(height - f * (height - 2)) - 0.5;
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  });

  if (samples.length < 2) {
    $("util-now").textContent = "";
    return;
  }

  const span = Math.max(1, samples[samples.length - 1].t - samples[0].t);
  const x = (sample) => ((sample.t - samples[0].t) / span) * (width - 1);
  const y = (value) => height - 1 - (clamp(value, 0, 100) / 100) * (height - 3);

  const trace = (key, colour) => {
    const points = samples.filter((s) => s[key] !== null && s[key] !== undefined);
    if (points.length < 2) return false;
    context.strokeStyle = colour;
    context.lineWidth = 1.5;
    context.beginPath();
    points.forEach((sample, i) => {
      const px = x(sample), py = y(sample[key]);
      i ? context.lineTo(px, py) : context.moveTo(px, py);
    });
    context.stroke();
    return true;
  };

  const warn = style.getPropertyValue("--warn").trim() || "#ffb648";
  const hasCpu = trace("cpu", accent);
  const hasGpu = trace("gpu", good);
  const hasEnc = trace("enc", warn);

  const latest = (key) => {
    for (let i = samples.length - 1; i >= 0; i--) {
      if (samples[i][key] != null) return samples[i][key];
    }
    return null;
  };
  const bits = [];
  if (hasCpu) bits.push(`CPU ${Math.round(latest("cpu"))}%`);
  if (hasGpu) bits.push(`GPU ${Math.round(latest("gpu"))}%`);
  if (hasEnc) bits.push(`encoder ${Math.round(latest("enc"))}%`);
  $("util-now").textContent = bits.join("   ·   ");
  // The encoder is separate silicon from the shaders, so a hardware render can
  // sit at 100% encoder while "GPU" barely moves. Saying which is which stops
  // that reading as an idle card.
  $("util-note").textContent = state.render.hasGpu
    ? "CPU · GPU (graphics) · encoder (NVENC) — a hardware render loads the encoder, not the shaders."
    : "GPU load needs nvidia-smi on PATH; CPU only here.";
}

async function pollRender() {
  let status;
  try {
    status = await api("/api/render?since=" + state.render.count);
  } catch {
    return;
  }

  const running = status.state === "probing" || status.state === "rendering";
  $("btn-render").disabled = running;
  $("btn-cancel").disabled = !running;
  const bar = $("progress-bar");
  bar.style.width = (status.progress.pct || 0) + "%";
  bar.classList.toggle("done", status.state === "done");
  bar.classList.toggle("failed", status.state === "error" || status.state === "cancelled");

  const p = status.progress;
  $("progress-text").textContent =
    status.state === "rendering"
      ? `${fmt(p.elapsed)} / ${fmt(p.total)}  (${Math.round(p.pct)}%)${p.speed ? "  @ " + p.speed : ""}`
      : status.state === "probing" ? "Analysing clips..."
      : status.state === "done" ? "Finished."
      : status.state === "error" ? "Failed."
      : status.state === "cancelled" ? "Cancelled."
      : "Idle";

  drawStages(status.stages);

  // The server hands back only what we have not seen; a reset job rewinds us.
  if (status.log_total < state.render.count) {
    state.render.log = [];
    state.render.count = 0;
  } else {
    state.render.count = status.log_total;
    appendLog(status.log || []);
  }

  state.render.samples = status.samples || [];
  state.render.hasGpu = !!status.has_gpu;
  drawUtil();

  $("done-actions").style.display = status.state === "done" ? "flex" : "none";

  if (!running) {
    clearInterval(state.poll);
    if (status.state === "done") toast("Render complete.");
    if (status.state === "error") toast(status.error, true);
  }
}

// ---------------------------------------------------------------- boot

projectToForm();
showAutoEditorState();
loadEncoders();
loadTemplates();
adoptRunningBalance();
updatePickerCount();
refreshAll();
showPage("settings");
pollRender();
