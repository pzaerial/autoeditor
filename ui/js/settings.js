import { showAutoEditorState } from "./balance.js";
import { browse, choosePath } from "./picker.js";
import { blankProject, probe, state } from "./state.js";
import { $, api, el, post, toast } from "./util.js";
import { refreshAll } from "./views.js";

// ---------------------------------------------------------------- settings: form binding

const FIELDS = [
  ["out-resolution", "output", "resolution", "string"],
  ["out-fps", "output", "fps", "int"],
  ["out-encoder", "output", "encoder", "string"],
  ["out-quality", "output", "quality", "opt"],
  ["def-join", "defaults", "join", "string"],
  ["def-crossfade", "defaults", "crossfade", "float"],
  ["def-fade", "defaults", "fade", "float"],
  ["def-audio-overlap", "defaults", "audio_overlap", "float"],
  ["def-fade-in", "defaults", "fade_in", "float"],
  ["def-fade-out", "defaults", "fade_out", "float"],
  ["def-trim", "defaults", "trim_silence", "bool"],
  ["bal-enabled", "balance", "enabled", "bool"],
  ["bal-target", "balance", "target_lufs", "float"],
  ["def-audio-blend", "defaults", "audio_blend", "opt"],
  ["def-audio-lead", "defaults", "audio_lead", "float"],
  ["sil-threshold", "silence", "threshold_db", "float"],
  ["sil-padding", "silence", "padding", "float"],
  ["sil-min-silence", "silence", "min_silence", "float"],
  ["sil-min-segment", "silence", "min_segment", "float"],
];

export function formToProject() {
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

export function projectToForm() {
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
export function showProjectName() {
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
export function showGroupStates() {
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

export async function checkOutput(target) {
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

export function outputToForm() {
  const { folder, name, ext } = splitOutput(state.project.output.file);
  $("out-folder").value = folder;
  $("out-name").value = name;
  const formats = [...$("out-format").options].map((o) => o.value);
  $("out-format").value = formats.includes(ext) ? ext : ".mp4";
  $("render-out-file").textContent = state.project.output.file || "(not set)";
}

export function outputFromForm() {
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

// ---------------------------------------------------------------- encoders
//
// A hardware encoder can be compiled into ffmpeg and still fail to open on a
// machine without that card. Asking once, at startup, keeps the list honest
// instead of letting a wrong choice surface as a failed render.

let encoders = [];

export async function loadEncoders() {
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

export async function loadTemplates(select) {
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

export async function saveProject(path) {
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
