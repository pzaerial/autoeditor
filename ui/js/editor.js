import { renderClipTable } from "./clips.js";
import { applyPreviewGain, resetSeekTarget, showGainTotal, stopPlayback, video } from "./preview.js";
import { renderClipList } from "./rail.js";
import { drawRegions, setMarking } from "./regions.js";
import { joinDuration, keptDuration, probe, state } from "./state.js";
import { $, clamp, fmt, mediaUrl } from "./util.js";
import { refreshAll, removeClip } from "./views.js";
import { resetView } from "./zoom.js";

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
export function previewProblem(info) {
  if (!info) return "";
  if (info.preview_note) return info.preview_note;
  if (codecSupported(info.vcodec) === false) {
    return `This window cannot decode ${String(info.vcodec).toUpperCase()} video ` +
      "— it needs hardware support that is not available here.";
  }
  return "";
}

export const STILL_EDITABLE =
  "Regions can still be set from the filmstrip and the time fields below — " +
  "only playback is missing. The render is not affected.";

export function showFallback(message) {
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
export function checkPreviewDecoded() {
  const clip = state.project.clips[state.selected];
  if (!clip || video.classList.contains("hidden")) return;
  if (video.videoWidth > 0 && video.videoHeight > 0) return;
  const codec = ((state.probes[clip.path] || {}).vcodec || "").toUpperCase();
  showFallback(
    (codec ? `${codec} video ` : "This file ") +
    "loaded but decoded no picture in this window. " + STILL_EDITABLE
  );
}

export async function selectClip(index) {
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

  resetSeekTarget();
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
  clip.join_duration = joinDuration(clip.join, d);
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

export function currentDuration() {
  const clip = state.project.clips[state.selected];
  const info = clip ? state.probes[clip.path] : null;
  return (info && info.duration) || video.duration || 0;
}

// ---------------------------------------------------------------- join audio
//
// An overlap is paid for out of source either side of the cut: the outgoing
// clip plays on past its out point, the incoming one starts before its in
// point. A clip used to its last frame has nothing to give, so the same
// arithmetic the renderer uses is mirrored here to say so before you render.

export function blendLength(clip) {
  return clip.audio_blend === null || clip.audio_blend === undefined
    ? clip.join_duration || 0
    : clip.audio_blend;
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
export function overlapReport(index) {
  const clip = state.project.clips[index];
  const before = state.project.clips[index - 1];
  if (!clip || !before) return null;

  // Mirrors audio_layout() in graph.py; keep the two in step.
  const ahead = clip.join === "audio overlap" ? (clip.join_duration || 0) : 0;
  const span = ahead ? 0 : (clip.join_duration || 0);
  const half = (blendLength(clip) - span) / 2;
  const lead = clip.audio_lead || 0;
  const wantHead = ahead + lead + (ahead ? 0 : half);
  const wantTail = ahead ? 0 : half - lead;
  if (Math.abs(wantHead) < 1e-9 && Math.abs(wantTail) < 1e-9) return null;

  // An `audio overlap` join pays for its head out of the picture it gives up,
  // so that much is always there -- no handles needed.
  const room = handles(clip).head + ahead;
  const roomBefore = handles(before).tail;
  const head = Math.min(Math.max(wantHead, -0.45 * keptDuration(clip)), room);
  const tail = Math.min(Math.max(wantTail, -0.45 * keptDuration(before)), roomBefore);
  return {
    ahead,
    asked: span + wantHead + wantTail,
    granted: Math.max(0, span + head + tail),
    // Silence trimming only shortens clips, so it can only free up more.
    estimate: clip.trim_silence || before.trim_silence,
  };
}

export function syncJoinAudio() {
  const index = state.selected;
  const clip = state.project.clips[index];
  const bar = $("join-audio-bar");
  if (!clip || index < 1) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  $("clip-audio-blend").value =
    clip.audio_blend === null || clip.audio_blend === undefined ? "" : clip.audio_blend;
  $("clip-audio-blend").placeholder = `follow ${clip.join_duration || 0}`;
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
      (report.ahead ? `, ${report.ahead.toFixed(2)}s of this clip's picture given up` : "") +
      (report.estimate ? " (before silence trimming)" : "");
    note.className = "hint ok-text";
  }
}

function setJoinAudio() {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  const raw = $("clip-audio-blend").value.trim();
  clip.audio_blend = raw === "" ? null : Math.max(0, parseFloat(raw) || 0);
  clip.audio_lead = parseFloat($("clip-audio-lead").value) || 0;
  syncJoinAudio();
  refreshAll();
}

$("clip-audio-blend").addEventListener("change", setJoinAudio);
$("clip-audio-lead").addEventListener("change", setJoinAudio);

export function syncGainControls() {
  const clip = state.project.clips[state.selected];
  const db = (clip && clip.audio_gain_db) || 0;
  $("clip-gain").value = db;
  $("clip-gain-slider").value = clamp(db, -24, 24);
  showGainTotal();
  applyPreviewGain();
}

export function setClipGain(db) {
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
