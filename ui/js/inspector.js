import { library, newEffect, newTransition, transitionDef } from "./library.js";
import { audioFollowsPicture, isTransition, layout, state, trackAt } from "./state.js";
import { $, el, fmt } from "./util.js";
import { removeEntry } from "./timeline.js";

// ---------------------------------------------------------------- inspector
//
// Whatever is selected on the timeline, and what can be done to it. Every
// control here is built from the library the engine publishes -- an effect's
// own parameters decide its inputs, their units and their bounds -- so adding
// an effect to `effects.py` gives it a control in the app without this file
// knowing its name. That is the whole point of having a library rather than a
// fixed set of fields.

let onChange = () => {};

export function bindInspector(handler) {
  onChange = handler || onChange;
}

export function drawInspector() {
  const box = $("tl-inspector");
  if (!box) return;
  box.innerHTML = "";

  const track = trackAt(state.selected.track);
  const entry = track ? track.entries[state.selected.entry] : null;
  if (!entry) {
    box.appendChild(el("div", "empty-note",
      "Select a clip or a transition on the timeline."));
    return;
  }
  if (isTransition(entry)) drawTransition(box, track, entry);
  else drawClip(box, track, entry);
}

// ---------------------------------------------------------------- clips

function drawClip(box, track, clip) {
  box.appendChild(heading(clip.label, clip.path));

  const placed = layout(track).find((p) => p.clip === clip);
  const info = state.probes[clip.path] || {};
  const whole = info.duration || 0;

  const facts = el("div", "facts");
  if (placed) {
    facts.appendChild(fact("On screen",
      `${fmt(placed.start)} → ${fmt(placed.start + placed.length)}`));
    facts.appendChild(fact("Length", fmt(placed.length)));
  }
  facts.appendChild(fact("Source", whole ? fmt(whole) : "unknown"));
  box.appendChild(facts);

  const range = el("div", "grid-2");
  range.appendChild(numberField("In", clip.source_in || 0, 0.1, (v) => {
    const out = clip.source_out == null ? whole : clip.source_out;
    clip.source_in = Math.max(0, Math.min(v, out - 0.25));
  }));
  range.appendChild(numberField(
    "Out", clip.source_out == null ? whole : clip.source_out, 0.1, (v) => {
      clip.source_out = Math.min(whole || v, Math.max(v, (clip.source_in || 0) + 0.25));
    }));
  box.appendChild(range);

  // Pinning is what takes a clip out of the sequence and puts it at a time of
  // its own -- an overlay, a bed, a title. Unpinning returns it to the strip.
  const pin = el("label", "check");
  const box2 = el("input");
  box2.type = "checkbox";
  box2.checked = clip.start != null;
  box2.addEventListener("change", () => {
    clip.start = box2.checked ? (placed ? placed.start : 0) : null;
    onChange();
  });
  pin.appendChild(box2);
  pin.appendChild(el("span", null, "Pin to a time of its own"));
  box.appendChild(pin);
  if (clip.start != null) {
    box.appendChild(numberField("Starts at", clip.start, 0.1, (v) => {
      clip.start = Math.max(0, v);
    }));
  }

  box.appendChild(el("h3", null, "Effects"));
  const list = el("div", "effect-list");
  (clip.effects || []).forEach((effect, index) => {
    list.appendChild(effectRow(clip, effect, index));
  });
  if (!(clip.effects || []).length) {
    list.appendChild(el("div", "hint", "Nothing applied to this clip yet."));
  }
  box.appendChild(list);
  box.appendChild(addEffect(clip));

  const remove = el("button", "ghost danger", "Remove clip");
  remove.addEventListener("click", () => {
    removeEntry(track, state.selected.entry);
    state.selected = { track: -1, entry: -1 };
    onChange();
  });
  box.appendChild(remove);
}

function effectRow(clip, effect, index) {
  const def = library.effects.find((e) => e.name === effect.name);
  const row = el("div", "effect-row");
  const head = el("div", "row between");
  head.appendChild(el("span", "effect-name", effect.name));
  const drop = el("button", "icon ghost", "×");
  drop.title = "Remove this effect";
  drop.addEventListener("click", () => {
    clip.effects.splice(index, 1);
    onChange();
  });
  head.appendChild(drop);
  row.appendChild(head);

  if (def && def.note) row.appendChild(el("div", "hint", def.note));

  (def ? def.params : []).forEach((param) => {
    row.appendChild(paramField(param, effect.params[param.name], (value) => {
      effect.params[param.name] = value;
    }));
  });
  return row;
}

function addEffect(clip) {
  const row = el("div", "row tight");
  const pick = el("select");
  pick.appendChild(new Option("Add an effect…", ""));
  library.effects
    .filter((e) => e.kind !== "marker")
    .forEach((e) => pick.appendChild(new Option(e.name, e.name)));
  pick.addEventListener("change", () => {
    if (!pick.value) return;
    clip.effects = clip.effects || [];
    if (!clip.effects.some((e) => e.name === pick.value)) {
      clip.effects.push(newEffect(pick.value));
    }
    pick.value = "";
    onChange();
  });
  row.appendChild(pick);
  return row;
}

// ---------------------------------------------------------------- transitions

function drawTransition(box, track, joins) {
  box.appendChild(heading(joins.kind, "between the clips either side"));

  const def = transitionDef(joins.kind);
  if (def.note) box.appendChild(el("div", "hint", def.note));

  const kind = el("label", "field");
  kind.appendChild(el("span", null, "Transition"));
  const pick = el("select");
  library.transitions.forEach((t) =>
    pick.appendChild(new Option(t.name, t.name)));
  pick.value = joins.kind;
  pick.addEventListener("change", () => {
    const fresh = newTransition(pick.value);
    joins.kind = fresh.kind;
    // Keep a duration that was deliberately set; take the new default only
    // when moving off a cut, which has none to keep.
    if (!joins.duration) joins.duration = fresh.duration;
    onChange();
  });
  kind.appendChild(pick);
  box.appendChild(kind);

  if (joins.kind !== "cut") {
    box.appendChild(numberField("Seconds", joins.duration || 0, 0.1, (v) => {
      joins.duration = Math.max(0, v);
    }));
  }

  // The sound's own timing. Blank means it follows the picture, which is what
  // an ordinary transition wants; filling either in is how a J or L cut is made.
  box.appendChild(el("h3", null, "Sound"));
  box.appendChild(el("div", "hint",
    "Leave these blank and the sound changes hands with the picture. " +
    "A positive lead is a J-cut — heard before it is seen."));

  const audio = el("div", "grid-2");
  audio.appendChild(optionalField(
    "Length (s)", joins.audio_duration, joins.duration || 0, (v) => {
      joins.audio_duration = v;
    }));
  audio.appendChild(optionalField("Lead (s)", joins.audio_lead, 0, (v) => {
    joins.audio_lead = v;
  }));
  box.appendChild(audio);

  const note = el("div", "hint");
  note.textContent = audioFollowsPicture(joins)
    ? "Sound follows the picture."
    : "Sound has a timeline of its own here.";
  box.appendChild(note);

  const remove = el("button", "ghost", "Make it a cut");
  remove.addEventListener("click", () => {
    removeEntry(track, state.selected.entry);
    state.selected = { track: -1, entry: -1 };
    onChange();
  });
  box.appendChild(remove);
}

// ---------------------------------------------------------------- fields

function heading(title, sub) {
  const head = el("div", "inspector-head");
  head.appendChild(el("h2", null, title));
  if (sub) head.appendChild(el("div", "path", sub));
  return head;
}

function fact(name, value) {
  const row = el("div", "fact");
  row.appendChild(el("span", "k", name));
  row.appendChild(el("span", "v mono", value));
  return row;
}

function numberField(label, value, step, apply) {
  const field = el("label", "field");
  field.appendChild(el("span", null, label));
  const input = el("input", "mono");
  input.type = "number";
  input.step = String(step);
  input.value = round(value);
  input.addEventListener("change", () => {
    apply(parseFloat(input.value) || 0);
    onChange();
  });
  field.appendChild(input);
  return field;
}

/** A number that may be left unset, where unset means "follow the default". */
function optionalField(label, value, placeholder, apply) {
  const field = el("label", "field");
  field.appendChild(el("span", null, label));
  const input = el("input", "mono");
  input.type = "number";
  input.step = "0.1";
  input.placeholder = String(round(placeholder));
  input.value = value == null ? "" : round(value);
  input.addEventListener("change", () => {
    apply(input.value.trim() === "" ? null : parseFloat(input.value) || 0);
    onChange();
  });
  field.appendChild(input);
  return field;
}

function paramField(param, value, apply) {
  if (param.kind === "bool") {
    const wrap = el("label", "check");
    const input = el("input");
    input.type = "checkbox";
    input.checked = !!value;
    input.addEventListener("change", () => {
      apply(input.checked);
      onChange();
    });
    wrap.appendChild(input);
    wrap.appendChild(el("span", null, param.name));
    return wrap;
  }
  if (param.kind === "choice") {
    const field = el("label", "field");
    field.appendChild(el("span", null, param.name));
    const pick = el("select");
    (param.choices || []).forEach((c) => pick.appendChild(new Option(c, c)));
    pick.value = value;
    pick.addEventListener("change", () => {
      apply(pick.value);
      onChange();
    });
    field.appendChild(pick);
    return field;
  }

  const field = el("label", "field");
  const name = param.unit ? `${param.name} (${param.unit})` : param.name;
  field.appendChild(el("span", null, name));
  const input = el("input", "mono");
  input.type = "number";
  input.step = String(param.step || 0.1);
  if (param.min != null) input.min = String(param.min);
  if (param.max != null) input.max = String(param.max);
  input.value = value == null ? param.default : value;
  input.title = param.note || "";
  input.addEventListener("change", () => {
    let v = parseFloat(input.value) || 0;
    if (param.min != null) v = Math.max(param.min, v);
    if (param.max != null) v = Math.min(param.max, v);
    input.value = v;
    apply(v);
    onChange();
  });
  field.appendChild(input);
  return field;
}

const round = (v) => Math.round((v || 0) * 1000) / 1000;
