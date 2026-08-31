import { clipGain } from "./library.js";
import { layout, projectDuration, state, tracks } from "./state.js";
import { drawPlayhead } from "./timeline.js";
import { $, fmt, fmtPrecise } from "./util.js";

// ---------------------------------------------------------------- preview
//
// One video element follows the playhead across the whole timeline, swapping
// its source whenever the playhead crosses into another clip. That is honest
// about ordering, timing and which layer is on top, and it is wrong about only
// one thing: it shows the outgoing clip through a crossfade rather than the
// blend, because compositing several decoders frame by frame in the browser is
// a different and much more fragile piece of machinery. The ruler marks the
// boundary so a transition is still visible while it is being set.

const video = $("preview");
let seekPending = null;
let seekExact = false;
let ticking = false;
let current = null;        // the placement the element is showing
let audioGraph = null;

export function previewProblem(info) {
  if (!info) return "";
  if (info.error) return info.error;
  if (info.playable === false) return "this format cannot be previewed";
  return "";
}

export const STILL_EDITABLE =
  "The clip is still part of the edit and will render normally.";

export function showFallback(message) {
  const fallback = $("video-fallback");
  if (!fallback) return;
  fallback.textContent = message;
  fallback.classList.toggle("hidden", !message);
  video.classList.toggle("hidden", !!message);
}

function ensureAudioGraph() {
  if (audioGraph !== null) return audioGraph || null;
  try {
    const Context = window.AudioContext || window.webkitAudioContext;
    const context = new Context();
    const source = context.createMediaElementSource(video);
    const gain = context.createGain();
    source.connect(gain).connect(context.destination);
    audioGraph = { context, gain };
  } catch {
    audioGraph = false;
  }
  return audioGraph || null;
}

// ---------------------------------------------------------------- what to show

/**
 * The clip the playhead is over, on the topmost visible video track.
 *
 * Top-most because that is what a viewer would see: later tracks cover earlier
 * ones, which is the same order the compositor overlays them in.
 */
export function clipAt(seconds) {
  let found = null;
  tracks().forEach((track, trackIndex) => {
    if (track.kind !== "video" || track.hidden) return;
    layout(track).forEach((placed) => {
      if (seconds >= placed.start - 1e-6 && seconds < placed.start + placed.length) {
        found = { track: trackIndex, placed };
      }
    });
  });
  return found;
}

/** Where in a clip's source the timeline time `seconds` lands. */
function sourceTimeOf(placed, seconds) {
  return (placed.clip.source_in || 0) + (seconds - placed.start);
}

export function showAt(seconds, { play = false } = {}) {
  const total = projectDuration();
  const at = Math.max(0, Math.min(seconds, total));
  state.playheadAt = at;
  drawPlayhead(at);
  readout(at, total);

  const found = clipAt(at);
  if (!found) {
    current = null;
    video.removeAttribute("src");
    video.load();
    showFallback("Nothing on screen at this point.");
    return;
  }

  const info = state.probes[found.placed.clip.path] || {};
  const problem = previewProblem(info);
  if (problem) {
    current = found;
    showFallback(`This clip cannot be previewed: ${problem}. ` + STILL_EDITABLE);
    return;
  }
  showFallback("");

  const wanted = "/media?path=" + encodeURIComponent(found.placed.clip.path);
  const same = current && current.placed.clip === found.placed.clip;
  current = found;
  applyPreviewGain();

  if (!same || !video.src.endsWith(encodeURIComponent(found.placed.clip.path))) {
    video.src = wanted;
    video.addEventListener("loadedmetadata", () => {
      seekTo(sourceTimeOf(found.placed, at), true);
      if (play) startPlayback();
    }, { once: true });
    return;
  }
  seekTo(sourceTimeOf(found.placed, at), !play);
  if (play) startPlayback();
}

function readout(at, total) {
  const node = $("time-readout");
  if (node) node.textContent = `${fmtPrecise(at)} / ${fmt(total)}`;
}

/**
 * Show the first frame once there is something to show.
 *
 * Called on every refresh, but it only acts when the preview is empty -- a
 * seek on every keystroke would fight the person typing. Opening a project is
 * the case that matters: the timeline fills in, and the screen should stop
 * saying there is nothing on it.
 */
export function refreshIfEmpty() {
  if (current) return;
  if (!clipAt(state.playheadAt || 0)) return;
  showAt(state.playheadAt || 0);
}

// ---------------------------------------------------------------- seeking

export function seekTo(seconds, exact) {
  if (!video.src || video.classList.contains("hidden")) return;
  seekPending = seconds;
  seekExact = !!exact;
  if (video.seeking) return;
  requestAnimationFrame(() => {
    if (seekPending === null) return;
    const target = seekPending;
    const precise = seekExact;
    seekPending = null;
    try {
      if (!precise && video.fastSeek) video.fastSeek(target);
      else video.currentTime = target;
    } catch { /* seeking before metadata is loaded */ }
  });
}

video.addEventListener("seeked", () => {
  if (seekPending === null) return;
  const target = seekPending;
  const precise = seekExact;
  seekPending = null;
  try {
    if (!precise && video.fastSeek) video.fastSeek(target);
    else video.currentTime = target;
  } catch { /* ignore */ }
});

// ---------------------------------------------------------------- playback

export function stopPlayback() {
  if (video.src && !video.paused) video.pause();
}

/**
 * Follow the playhead in media time, not on a timer.
 *
 * A wall-clock interval drifts every time the decoder stalls, and the drift is
 * exactly what a person is watching for when they set a transition.
 */
function tick() {
  if (video.paused) {
    ticking = false;
    return;
  }
  if (current) {
    const into = video.currentTime - (current.placed.clip.source_in || 0);
    const at = current.placed.start + into;
    const past = at >= current.placed.start + current.placed.length - 0.02;
    if (past) {
      // Straight into whatever is on screen next, so playback runs the whole
      // edit rather than stopping at every cut.
      const next = current.placed.start + current.placed.length + 0.01;
      if (next >= projectDuration()) {
        video.pause();
        showAt(projectDuration());
        return;
      }
      showAt(next, { play: true });
      requestAnimationFrame(tick);
      return;
    }
    if (!state.drag) {
      state.playheadAt = at;
      drawPlayhead(at);
      readout(at, projectDuration());
    }
  }
  requestAnimationFrame(tick);
}

function startTicking() {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(tick);
}

export function startPlayback() {
  const graph = ensureAudioGraph();
  if (graph && graph.context.state === "suspended") graph.context.resume();
  applyPreviewGain();
  video.play().catch(() => {});
}

export function togglePlayback() {
  if (!video.src) {
    showAt(state.playheadAt || 0, { play: true });
    return;
  }
  if (video.paused) startPlayback();
  else video.pause();
}

// ---------------------------------------------------------------- gain

function previewGainDb() {
  if (!current) return 0;
  const track = tracks()[current.track];
  return clipGain(current.placed.clip) + ((track && track.gain_db) || 0);
}

export function applyPreviewGain() {
  const db = previewGainDb();
  const ratio = Math.pow(10, db / 20);
  const muted = $("clip-mute") && $("clip-mute").checked;
  const graph = ensureAudioGraph();
  if (graph) {
    const now = graph.context.currentTime;
    graph.gain.gain.cancelScheduledValues(now);
    graph.gain.gain.setTargetAtTime(muted ? 0 : ratio, now, 0.02);
  } else {
    video.volume = muted ? 0 : Math.min(1, ratio);
  }
  const total = $("clip-gain-total");
  if (total) {
    total.textContent = `${db > 0 ? "+" : ""}${db.toFixed(1)} dB` +
      (!graph && db > 0 ? "  (preview cannot boost)" : "");
  }
}

video.addEventListener("play", () => {
  const button = $("play-toggle");
  if (button) button.textContent = "Pause";
  startTicking();
});
video.addEventListener("pause", () => {
  const button = $("play-toggle");
  if (button) button.textContent = "Play";
});
video.addEventListener("error", () => {
  if (!current || video.classList.contains("hidden")) return;
  const detail = video.error && video.error.message ? ` (${video.error.message})` : "";
  showFallback(`The preview could not open this file${detail}. ` + STILL_EDITABLE);
});

const play = $("play-toggle");
if (play) play.addEventListener("click", togglePlayback);
const mute = $("clip-mute");
if (mute) mute.addEventListener("change", applyPreviewGain);
