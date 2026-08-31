import { api } from "./util.js";

// ---------------------------------------------------------------- the library
//
// Effects and transitions are declared once, in `autoeditor/effects.py`, and
// fetched here. The inspector builds its controls from this, so an effect added
// to the engine gets a control in the app without anything here being told
// about it by name.
//
// The fallbacks below are only what layout needs before the fetch lands -- how
// a transition sits on the timeline -- so an early draw cannot be wrong about
// where a clip is. They are never used to build a control.

const FALLBACK = {
  cut: { overlaps: false, trims_incoming: false },
  crossfade: { overlaps: true, trims_incoming: false },
  "dip to black": { overlaps: false, trims_incoming: false },
  "audio overlap": { overlaps: false, trims_incoming: true },
};

export const library = { effects: [], transitions: [], loaded: false };

export async function loadLibrary() {
  try {
    const data = await api("/api/library");
    library.effects = data.effects || [];
    library.transitions = data.transitions || [];
    library.loaded = true;
  } catch {
    library.loaded = false;
  }
  return library;
}

function find(list, name) {
  const wanted = String(name || "").toLowerCase();
  return list.find(
    (item) => item.name === wanted || (item.aliases || []).includes(wanted)
  );
}

export function effectDef(name) {
  return find(library.effects, name) || null;
}

export function transitionDef(name) {
  return find(library.transitions, name) || FALLBACK[name] || FALLBACK.cut;
}

/** Seconds two clips overlap when joined by this transition. */
export function overlapOf(transition) {
  if (!transition) return 0;
  return transitionDef(transition.kind).overlaps
    ? Math.max(0, transition.duration || 0)
    : 0;
}

/** Seconds of picture the incoming clip gives up to this transition. */
export function headTrimOf(transition) {
  if (!transition) return 0;
  return transitionDef(transition.kind).trims_incoming
    ? Math.max(0, transition.duration || 0)
    : 0;
}

/** A new effect with the library's own defaults filled in. */
export function newEffect(name) {
  const def = effectDef(name);
  const params = {};
  (def ? def.params : []).forEach((p) => (params[p.name] = p.default));
  return { name: def ? def.name : name, params };
}

/** A new transition with the library's own defaults filled in. */
export function newTransition(kind) {
  const def = transitionDef(kind);
  const first = (def.params || [])[0];
  return {
    type: "transition",
    kind,
    duration: first ? Number(first.default) || 0 : 0,
    audio_duration: null,
    audio_lead: null,
  };
}

/** What a clip's own audio level is, the one number balance and a person share. */
export function clipGain(clip) {
  const found = (clip.effects || []).find((e) => e.name === "volume");
  return found ? Number(found.params.db) || 0 : 0;
}

export function setClipGain(clip, db) {
  clip.effects = clip.effects || [];
  const found = clip.effects.find((e) => e.name === "volume");
  if (db === 0) {
    clip.effects = clip.effects.filter((e) => e.name !== "volume");
  } else if (found) {
    found.params.db = db;
  } else {
    clip.effects.push({ name: "volume", params: { db } });
  }
}

export function hasEffect(clip, name) {
  return (clip.effects || []).some((e) => e.name === name);
}

export function toggleEffect(clip, name, on) {
  clip.effects = clip.effects || [];
  if (on && !hasEffect(clip, name)) clip.effects.push(newEffect(name));
  if (!on) clip.effects = clip.effects.filter((e) => e.name !== name);
}
