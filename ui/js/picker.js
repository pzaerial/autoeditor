import { runBalance } from "./balance.js";
import { previewProblem } from "./preview.js";
import { showPage } from "./pages.js";
import { clipsOf, probe, state, tracks, trackAt } from "./state.js";
import { appendClips } from "./tracklanes.js";
import { $, api, el, fmt, toast } from "./util.js";
import { refreshAll } from "./views.js";

// ---------------------------------------------------------------- clips: browser

export async function browse(path) {
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

export function updatePickerCount() {
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
  return allClips()
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
export function choosePath({ title, subtitle, start, filename, extension } = {}) {
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

export function openPicker(relinkIndex) {
  const clip = relinkIndex >= 0 ? allClips()[relinkIndex] : null;
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

/** Every clip in the project, in the order the backend indexes them. */
function allClips() {
  return (state.project.tracks || []).flatMap((t) =>
    (t.entries || []).filter((e) => e.type !== "transition"));
}

export function closePicker() {
  $("picker").classList.add("hidden");
  state.browse.relink = -1;
  // A dismissed request must still settle, or its caller waits for ever.
  if (state.picker) finishPicker(null);
}

/** Point a clip at a new file, keeping its label, regions and joins. */
export async function relinkClip(index, path) {
  const clip = allClips()[index];
  clip.path = path;
  clip.missing = false;
  delete state.probes[path];
  const info = await probe(path);

  // A shorter replacement cannot keep an out point past its own end.
  const duration = info.duration || 0;
  if (duration) {
    if (clip.source_out != null) clip.source_out = Math.min(clip.source_out, duration);
    clip.source_in = Math.min(clip.source_in || 0, Math.max(0, duration - 0.25));
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
    toast(others.length ? `Relinked ${others.length + 1} clips.` : "Relinked.");
    return;
  }

  const picked = state.browse.files.filter((f) => state.browse.chosen.has(f.path));
  if (!picked.length) {
    toast("Nothing selected.");
    return;
  }
  // Onto the track being worked on, or the first video track if none is.
  const target = trackAt(state.selected.track) ||
    tracks().find((t) => t.kind === "video") || tracks()[0];
  if (!target) {
    toast("Add a track first.", true);
    return;
  }
  appendClips(target, picked.map((f) => f.path));
  picked.forEach((f) => probe(f.path));
  state.browse.chosen.clear();
  document.querySelectorAll("#browse-list input[type=checkbox]").forEach((b) => (b.checked = false));
  updatePickerCount();
  refreshAll();
  closePicker();
  toast(`Added ${picked.length} clip(s).`);
  // Levelling is on, so the clips just added should be levelled too -- that is
  // the point of it being a project setting rather than a one-off button.
  if (state.project.balance.enabled) runBalance({ onlyUnmeasured: true });
});

$("clips-to-edit").addEventListener("click", () => showPage("edit"));
