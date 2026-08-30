import { STILL_EDITABLE, checkPreviewDecoded, currentDuration, showFallback } from "./editor.js";
import { playheadTime } from "./regions.js";
import { state } from "./state.js";
import { $, fmt, fmtPrecise } from "./util.js";
import { followPlayhead, refreshScrubber, view } from "./zoom.js";

// ---------------------------------------------------------------- edit: preview audio
//
// The preview runs through a gain node so the dB trim you dial in is the level
// you hear. Without Web Audio a boost cannot be previewed, only a cut.

export const video = $("preview");
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

function clipGainDb() {
  const clip = state.project.clips[state.selected];
  return (clip && clip.audio_gain_db) || 0;
}

export function applyPreviewGain() {
  const db = clipGainDb();
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

export function showGainTotal() {
  const db = clipGainDb();
  const graph = ensureAudioGraph();
  $("clip-gain-total").textContent =
    `${db > 0 ? "+" : ""}${db.toFixed(1)} dB` +
    (!graph && db > 0 ? "  (preview cannot boost)" : "");
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

export function seekTo(seconds, exact) {
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

/** Forget where the last gesture pointed -- a new clip starts from nothing. */
export function resetSeekTarget() {
  lastTarget = null;
}

/** Land exactly on the last requested frame, so both streams start together. */
export function settleSeek(target) {
  if (target === undefined) target = lastTarget;
  lastTarget = null;
  if (target === null || target === undefined) return;
  if (!video.src || video.classList.contains("hidden")) return;
  seekPending = null;
  try {
    video.currentTime = target;
  } catch { /* not loaded yet */ }
}

export function stopPlayback() {
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

export function playRegion(region) {
  if (!video.src) return;
  updatePlayhead(region.start);
  settleSeek(region.start);
  stopAt = region.end;
  startPlayback();
}

export function startPlayback() {
  const graph = ensureAudioGraph();
  // An AudioContext starts suspended until a user gesture resumes it.
  if (graph && graph.context.state === "suspended") graph.context.resume();
  applyPreviewGain();
  video.play().catch(() => {});
}

export function playheadInView() {
  const v = view();
  const at = playheadTime();
  return at >= v.start && at <= v.end;
}

export function updatePlayhead(at) {
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
