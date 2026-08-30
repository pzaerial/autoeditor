import { blendLength } from "./editor.js";
import { browse, choosePath } from "./picker.js";
import { $, api, fmt } from "./util.js";
import { view } from "./zoom.js";

// ---------------------------------------------------------------- state

export const state = {
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
    clips: [],
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

export function keptDuration(clip) {
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

export const MIN_REGION = 0.25;
// The tightest the scrubber will zoom: half a second across the whole track.
export const MIN_SPAN = 0.5;

function clipDuration(clip) {
  const info = clip ? state.probes[clip.path] : null;
  return (info && info.duration) || 0;
}

/** Bounds a region may occupy: the clip, minus its neighbours. */
export function regionBounds(regions, index, duration) {
  const before = regions[index - 1];
  const after = regions[index + 1];
  return [before ? before.end : 0, after ? after.start : duration];
}

export function newRegion(start, end) {
  return { start, end, join: "cut", join_duration: 0 };
}

export function estimateTotal() {
  let total = 0;
  state.project.clips.forEach((clip, i) => {
    total += keptDuration(clip);
    if (i === 0) return;
    if (clip.join === "crossfade") total -= clip.join_duration || 0;
    total -= joinTrim(clip);
  });
  return Math.max(0, total);
}

// Silence trimming only resolves during a render, so the estimate reads high
// whenever any clip opts into it.
function estimateIsUpperBound() {
  return state.project.clips.some((c) => c.trim_silence);
}

export function estimateLabel() {
  const text = fmt(estimateTotal());
  return estimateIsUpperBound() ? `up to ${text} (before silence trimming)` : text;
}

export function joinLabel(clip) {
  return clip.join === "cut" ? "cut" : `${clip.join} ${clip.join_duration}s`;
}

export function audioFollowsPicture(clip) {
  if (clip.join === "audio overlap") return false;
  return !clip.audio_lead &&
    Math.abs(blendLength(clip) - (clip.join_duration || 0)) < 1e-9;
}

/** The default seconds for a join, matching what the parser would choose. */
export function joinDuration(join, defaults) {
  if (join === "cut") return 0;
  if (join === "fade") return defaults.fade;
  if (join === "audio overlap") return defaults.audio_overlap;
  return defaults.crossfade;
}

/**
 * How much of the timeline this clip takes up. An `audio overlap` join gives up
 * that many seconds of its own picture, so the estimate must not count them.
 */
export function joinTrim(clip) {
  return clip.join === "audio overlap" ? (clip.join_duration || 0) : 0;
}

export function audioJoinLabel(clip) {
  const lead = clip.audio_lead || 0;
  return `audio ${blendLength(clip)}s` +
    (lead ? ` @ ${lead > 0 ? "+" : ""}${lead}s` : "");
}
