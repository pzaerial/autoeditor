import { headTrimOf, overlapOf, transitionDef } from "./library.js";
import { api, fmt } from "./util.js";

// ---------------------------------------------------------------- state
//
// The project is a list of tracks, each an ordered list of entries: clips and
// the transitions between them. Where a clip *sits* is a property of the track,
// worked out by `layout` below, not a field on the clip -- which is what lets
// two clips share a moment, and a clip sit anywhere on a lane of its own.

export const state = {
  project: blankProject(),
  // Which entry is being edited: an index into a track, and into its entries.
  selected: { track: -1, entry: -1 },
  probes: {},         // path -> {duration, has_audio, playable, ...}
  drag: null,         // in-flight pointer gesture
  view: null,         // {start, end} window the timeline shows; null = all of it
  playing: null,      // {track, entry} the preview is following
  browse: { path: "", files: [], chosen: new Set(), loaded: false, relink: -1 },
  picker: null,
  template: "",
  page: "settings",
  poll: null,
  render: { log: [], count: 0, samples: [], hasGpu: false, output: "" },
};

export function blankProject() {
  return {
    title: "Untitled",
    output: { file: "", resolution: "1920x1080", fps: 60, encoder: "libx264", quality: null },
    defaults: {
      join: "crossfade", crossfade: 0.3, fade: 0.5, audio_overlap: 2, trim_silence: false,
      fade_in: 0.5, fade_out: 0.5, audio_blend: null, audio_lead: 0,
    },
    silence: { threshold_db: -30, padding: 0.5, min_silence: 1.0, min_segment: 0.5 },
    balance: { enabled: false, target_lufs: -14 },
    tracks: [newTrack("video", "Main")],
  };
}

export function newTrack(kind, name) {
  return { kind, name, gain_db: 0, muted: false, hidden: false, entries: [] };
}

export function newClip(path, label) {
  return {
    type: "clip",
    path,
    label: label || path.split(/[\\/]/).pop().replace(/\.[^.]+$/, ""),
    source_in: 0,
    source_out: null,
    start: null,
    link: null,
    missing: false,
    effects: [],
  };
}

// ---------------------------------------------------------------- probing

export async function probe(path) {
  if (state.probes[path]) return state.probes[path];
  try {
    const info = await api("/api/probe?path=" + encodeURIComponent(path));
    state.probes[path] = info;
    return info;
  } catch {
    return (state.probes[path] = { duration: 0, playable: false, error: "unreadable" });
  }
}

// ---------------------------------------------------------------- shape

export const isClip = (entry) => entry && entry.type !== "transition";
export const isTransition = (entry) => entry && entry.type === "transition";

export function tracks() {
  return state.project.tracks || [];
}

export function trackAt(index) {
  return tracks()[index] || null;
}

export function clipsOf(track) {
  return (track.entries || []).filter(isClip);
}

export function selectedEntry() {
  const track = trackAt(state.selected.track);
  if (!track) return null;
  return track.entries[state.selected.entry] || null;
}

/** How long a clip's chosen range of its source runs. */
export function sourceLength(clip) {
  const info = state.probes[clip.path];
  const whole = (info && info.duration) || 0;
  const out = clip.source_out == null ? whole : clip.source_out;
  return Math.max(0, out - (clip.source_in || 0));
}

/** How long a clip occupies the timeline, after any picture it gives up. */
export function clipLength(clip, before) {
  return Math.max(0, sourceLength(clip) - headTrimOf(before));
}

/**
 * Where every clip on a track sits.
 *
 * The same rule the engine applies, and it has to stay the same rule: clips are
 * sequential, each starting where the last ended minus the overlap of the
 * transition joining them, and a clip with an explicit `start` anchors there
 * instead. Anything the app draws that the engine would place elsewhere is a
 * lie about what will render.
 */
export function layout(track) {
  const out = [];
  let cursor = 0;
  let before = null;
  (track.entries || []).forEach((entry, index) => {
    if (isTransition(entry)) {
      before = entry;
      return;
    }
    const length = clipLength(entry, before);
    let start;
    if (entry.start != null) start = entry.start;
    else if (!out.length) start = 0;
    else start = cursor - overlapOf(before);
    start = Math.max(0, start);
    out.push({ index, clip: entry, start, length, before, after: null });
    cursor = start + length;
    before = null;
  });
  // A clip's `after` is the transition immediately following it, if any.
  out.forEach((placed) => {
    const next = track.entries[placed.index + 1];
    placed.after = isTransition(next) ? next : null;
  });
  return out;
}

export function trackDuration(track) {
  return layout(track).reduce((max, p) => Math.max(max, p.start + p.length), 0);
}

/**
 * How long the finished video runs. The picture decides -- a bed outlasting the
 * last frame is cut off rather than extending the edit -- which is the rule the
 * compositor applies, so the estimate matches the render.
 */
export function projectDuration() {
  const video = tracks().filter((t) => t.kind === "video" && !t.hidden);
  const pool = video.length ? video : tracks().filter((t) => !t.muted);
  return pool.reduce((max, t) => Math.max(max, trackDuration(t)), 0);
}

export function estimateTotal() {
  return projectDuration();
}

function estimateIsUpperBound() {
  return tracks().some((t) =>
    clipsOf(t).some((c) => (c.effects || []).some((e) => e.name === "trim silence"))
  );
}

export function estimateLabel() {
  const text = fmt(estimateTotal());
  return estimateIsUpperBound() ? `up to ${text} (before silence trimming)` : text;
}

// ---------------------------------------------------------------- labels

export function transitionLabel(transition) {
  if (!transition) return "cut";
  return transition.duration
    ? `${transition.kind} ${transition.duration}s`
    : transition.kind;
}

/** True when a transition's sound needs no timeline of its own. */
export function audioFollowsPicture(transition) {
  if (!transition) return true;
  const def = transitionDef(transition.kind);
  if (def.audio_mode && def.audio_mode !== "crossfade") return false;
  if (def.trims_incoming) return false;
  const blend = transition.audio_duration == null
    ? transition.duration || 0
    : transition.audio_duration;
  return !transition.audio_lead &&
    Math.abs(blend - (transition.duration || 0)) < 1e-9;
}

export function audioJoinLabel(transition) {
  const blend = transition.audio_duration == null
    ? transition.duration || 0
    : transition.audio_duration;
  const lead = transition.audio_lead || 0;
  return `audio ${blend}s` + (lead ? ` @ ${lead > 0 ? "+" : ""}${lead}s` : "");
}

// The tightest the timeline will zoom: half a second across the whole width.
export const MIN_SPAN = 0.5;
export const MIN_CLIP = 0.25;
