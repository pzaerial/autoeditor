import { transitionDef } from "./library.js";
import {
  MIN_CLIP, MIN_SPAN, audioFollowsPicture, isClip, isTransition, layout,
  projectDuration, state, trackAt, tracks,
} from "./state.js";
import { $, el, fmt } from "./util.js";

// ---------------------------------------------------------------- the timeline
//
// One lane per track, clips drawn where they actually sit. The old Edit page
// showed a rail of clips and a scrubber spanning one clip's *source*, which
// could say what was inside a clip but never how clips sat against each other
// -- so transitions and layers were invisible. Here the horizontal axis is the
// finished video, which is the only axis on which a transition is a thing you
// can see and point at.
//
// Everything drawn here is positioned by `layout()` in state.js, which is the
// same rule the compositor places clips with. Nothing in this file works out
// where a clip goes on its own.

let onChange = () => {};
let onSelect = () => {};
let onSeek = () => {};

export function bindTimeline(handlers) {
  onChange = handlers.change || onChange;
  onSelect = handlers.select || onSelect;
  onSeek = handlers.seek || onSeek;
}

// ---------------------------------------------------------------- the view

export function view() {
  const total = projectDuration() || 1;
  if (!state.view) return { start: 0, end: total, span: total };
  const start = Math.max(0, Math.min(state.view.start, total - MIN_SPAN));
  const end = Math.min(total, Math.max(state.view.end, start + MIN_SPAN));
  return { start, end, span: end - start };
}

export function resetView() {
  state.view = null;
}

export function zoomBy(factor, anchorSeconds) {
  const { start, end, span } = view();
  const total = projectDuration() || 1;
  const next = Math.max(MIN_SPAN, Math.min(total, span * factor));
  const anchor = anchorSeconds == null ? start + span / 2 : anchorSeconds;
  const ratio = span ? (anchor - start) / span : 0.5;
  let a = anchor - next * ratio;
  a = Math.max(0, Math.min(a, total - next));
  state.view = next >= total ? null : { start: a, end: a + next };
}

export function panBy(seconds) {
  const { start, end, span } = view();
  const total = projectDuration() || 1;
  if (span >= total) return;
  let a = Math.max(0, Math.min(start + seconds, total - span));
  state.view = { start: a, end: a + span };
}

/** Seconds -> fraction of the visible window. */
function ratioOf(seconds) {
  const { start, span } = view();
  return span ? (seconds - start) / span : 0;
}

function secondsAt(clientX) {
  // Measured against the strip area, not the whole body: the lane heads take a
  // fixed width on the left and are not part of the time axis.
  const strip = $("tl-scale");
  if (!strip) return 0;
  const box = strip.getBoundingClientRect();
  const { start, span } = view();
  return start + ((clientX - box.left) / Math.max(1, box.width)) * span;
}

// ---------------------------------------------------------------- drawing

export function drawTimeline() {
  const lanes = $("tl-lanes");
  if (!lanes) return;
  lanes.innerHTML = "";

  const total = projectDuration();
  const { start, end } = view();

  tracks().forEach((track, index) => {
    const lane = el("div", "tl-lane" + (track.kind === "audio" ? " audio" : ""));
    lane.dataset.track = String(index);

    lane.appendChild(laneHead(track, index));

    const strip = el("div", "tl-strip");
    strip.dataset.track = String(index);

    layout(track).forEach((placed) => {
      const left = ratioOf(placed.start);
      const right = ratioOf(placed.start + placed.length);
      if (right < -0.05 || left > 1.05) return;      // off screen

      const block = el(
        "div",
        "tl-clip" +
          (placed.clip.missing ? " missing" : "") +
          (isSelected(index, placed.index) ? " selected" : "")
      );
      block.style.left = `${left * 100}%`;
      block.style.width = `${Math.max(0.2, (right - left) * 100)}%`;
      block.dataset.track = String(index);
      block.dataset.entry = String(placed.index);

      block.appendChild(el("span", "grip left"));
      const body = el("div", "body");
      body.appendChild(el("div", "name", placed.clip.label));
      const marks = [];
      if (placed.clip.start != null) marks.push("pinned");
      (placed.clip.effects || []).forEach((e) =>
        marks.push(e.name === "volume"
          ? `${e.params.db > 0 ? "+" : ""}${e.params.db} dB` : e.name));
      if (marks.length) body.appendChild(el("div", "marks", marks.join(" · ")));
      block.appendChild(body);
      block.appendChild(el("span", "grip right"));
      block.title = `${placed.clip.label}\n${fmt(placed.start)} → ` +
        `${fmt(placed.start + placed.length)}  (${fmt(placed.length)})`;
      strip.appendChild(block);

      // The transition into this clip, drawn across the ground it covers.
      if (placed.before) {
        strip.appendChild(
          transitionMark(index, placed, track.kind === "audio")
        );
      }
    });

    lane.appendChild(strip);
    lanes.appendChild(lane);
  });

  drawRuler(start, end);
  drawPlayhead();
  const readout = $("tl-zoom-readout");
  if (readout) {
    readout.textContent = state.view
      ? `${fmt(start)} – ${fmt(end)} of ${fmt(total)}`
      : `all ${fmt(total)}`;
  }
}

function isSelected(track, entry) {
  return state.selected.track === track && state.selected.entry === entry;
}

function laneHead(track, index) {
  const head = el("div", "tl-head");
  const tag = track.kind === "video" ? "V" : "A";
  const order = tracks().filter((t) => t.kind === track.kind).indexOf(track) + 1;
  head.appendChild(el("span", "badge", `${tag}${order}`));

  const name = el("input", "name");
  name.value = track.name;
  name.addEventListener("change", () => {
    track.name = name.value.trim() || track.name;
    onChange();
  });
  head.appendChild(name);

  const toggles = el("div", "toggles");
  const eye = el("button", "icon" + (track.hidden ? " off" : ""),
    track.kind === "video" ? "👁" : "");
  if (track.kind === "video") {
    eye.title = track.hidden ? "Hidden — click to show" : "Visible";
    eye.addEventListener("click", () => {
      track.hidden = !track.hidden;
      onChange();
    });
    toggles.appendChild(eye);
  }
  const mute = el("button", "icon" + (track.muted ? " off" : ""), "🔊");
  mute.title = track.muted ? "Muted — click to unmute" : "Heard";
  mute.addEventListener("click", () => {
    track.muted = !track.muted;
    onChange();
  });
  toggles.appendChild(mute);

  const gain = el("input", "gain mono");
  gain.type = "number";
  gain.step = "0.5";
  gain.value = track.gain_db || 0;
  gain.title = "Level for everything on this track, in dB";
  gain.addEventListener("change", () => {
    track.gain_db = parseFloat(gain.value) || 0;
    onChange();
  });
  toggles.appendChild(gain);
  head.appendChild(toggles);
  return head;
}

/**
 * A transition, drawn over the ground it actually covers.
 *
 * On a video lane that is the overlap: the stretch where both pictures are on
 * screen at once, or -- for one that does not overlap -- a mark at the join.
 * On the audio side it is drawn where the *sound* changes hands, which is the
 * whole point of having the two on separate lanes: a J-cut is visible as the
 * offset it is, rather than a number in a form.
 */
function transitionMark(trackIndex, placed, isAudioLane) {
  const joins = placed.before;
  const def = transitionDef(joins.kind);
  const blend = joins.audio_duration == null
    ? joins.duration || 0 : joins.audio_duration;
  const lead = joins.audio_lead || 0;

  let from = placed.start;
  let to = placed.start + (def.overlaps ? joins.duration || 0 : 0);
  if (isAudioLane || !audioFollowsPicture(joins)) {
    // Sound centred `lead` before the picture, over its own length.
    const centre = placed.start + (def.overlaps ? (joins.duration || 0) / 2 : 0) - lead;
    from = centre - blend / 2;
    to = centre + blend / 2;
  }

  const mark = el(
    "div",
    "tl-trans" + (def.overlaps ? " blend" : " hard") +
      (isSelected(trackIndex, placed.index - 1) ? " selected" : "")
  );
  const left = ratioOf(from);
  const width = Math.max(0.004, ratioOf(to) - left);
  mark.style.left = `${left * 100}%`;
  mark.style.width = `${width * 100}%`;
  mark.dataset.track = String(trackIndex);
  mark.dataset.entry = String(placed.index - 1);
  mark.dataset.role = "transition";
  mark.title = `${joins.kind}${joins.duration ? " " + joins.duration + "s" : ""}` +
    (audioFollowsPicture(joins) ? "" : `\naudio ${blend}s, lead ${lead}s`);
  mark.appendChild(el("span", "label", joins.kind === "crossfade" ? "✕"
    : joins.kind === "dip to black" ? "▮" : joins.kind === "audio overlap" ? "♪" : "|"));
  return mark;
}

function drawRuler(start, end) {
  const ruler = $("tl-ruler");
  if (!ruler) return;
  ruler.innerHTML = "";
  const span = Math.max(0.001, end - start);
  // A tick roughly every 90px, rounded to something a person would count in.
  const width = ruler.getBoundingClientRect().width || 800;
  const rough = (span / Math.max(1, width / 90));
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
  const step = steps.find((s) => s >= rough) || 3600;
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) {
    const tick = el("div", "tick");
    tick.style.left = `${ratioOf(t) * 100}%`;
    tick.appendChild(el("span", null, fmt(t)));
    ruler.appendChild(tick);
  }
}

export function drawPlayhead(at) {
  const head = $("tl-playhead");
  if (!head) return;
  const seconds = at == null ? (state.playheadAt || 0) : at;
  state.playheadAt = seconds;
  const ratio = ratioOf(seconds);
  head.style.left = `${ratio * 100}%`;
  head.classList.toggle("hidden", ratio < 0 || ratio > 1);
}

// ---------------------------------------------------------------- gestures

const EDGE_PX = 7;

function hit(event) {
  const node = event.target.closest(".tl-clip, .tl-trans");
  if (!node) return null;
  const track = Number(node.dataset.track);
  const entry = Number(node.dataset.entry);
  if (node.dataset.role === "transition") return { kind: "transition", track, entry, node };
  const box = node.getBoundingClientRect();
  if (event.clientX - box.left <= EDGE_PX) return { kind: "trim-in", track, entry, node };
  if (box.right - event.clientX <= EDGE_PX) return { kind: "trim-out", track, entry, node };
  return { kind: "move", track, entry, node };
}

export function initTimeline() {
  const body = $("tl-body");
  if (!body) return;

  body.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const found = hit(event);
    if (!found) {
      // Empty ground: put the playhead there and show what is on screen at
      // that moment, which is what clicking a timeline is for.
      const at = Math.max(0, secondsAt(event.clientX));
      drawPlayhead(at);
      onSeek(at);
      onSelect(null);
      return;
    }
    body.setPointerCapture(event.pointerId);
    const track = trackAt(found.track);
    const entry = track.entries[found.entry];
    state.selected = { track: found.track, entry: found.entry };
    state.drag = {
      ...found,
      at: secondsAt(event.clientX),
      startedAt: secondsAt(event.clientX),
      original: JSON.parse(JSON.stringify(entry)),
      moved: false,
    };
    onSelect(state.selected);
    drawTimeline();
  });

  body.addEventListener("pointermove", (event) => {
    const drag = state.drag;
    if (!drag) {
      const found = hit(event);
      body.style.cursor = !found ? "default"
        : found.kind === "trim-in" || found.kind === "trim-out" ? "ew-resize"
        : "grab";
      return;
    }
    const now = secondsAt(event.clientX);
    const delta = now - drag.startedAt;
    if (Math.abs(delta) < 1e-4 && !drag.moved) return;
    drag.moved = true;
    applyDrag(drag, delta, now, event);
    drawTimeline();
  });

  const finish = (event) => {
    if (!state.drag) return;
    const moved = state.drag.moved;
    state.drag = null;
    body.style.cursor = "default";
    if (moved) onChange();
  };
  body.addEventListener("pointerup", finish);
  body.addEventListener("pointercancel", finish);

  body.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomBy(event.deltaY > 0 ? 1.2 : 1 / 1.2, secondsAt(event.clientX));
    drawTimeline();
  }, { passive: false });
}

function applyDrag(drag, delta, now, event) {
  const track = trackAt(drag.track);
  const entry = track.entries[drag.entry];
  if (!entry) return;

  if (drag.kind === "transition") {
    // Sideways sets the duration; with shift held it moves the *sound* instead,
    // which is how a J or L cut is made by hand.
    if (event.shiftKey) {
      entry.audio_lead = round(-(delta) + (drag.original.audio_lead || 0));
    } else {
      entry.duration = Math.max(0, round((drag.original.duration || 0) + delta));
    }
    return;
  }

  const info = state.probes[entry.path] || {};
  const whole = info.duration || 0;

  if (drag.kind === "trim-in") {
    const out = entry.source_out == null ? whole : entry.source_out;
    entry.source_in = clamp(round((drag.original.source_in || 0) + delta),
                            0, out - MIN_CLIP);
    return;
  }
  if (drag.kind === "trim-out") {
    const base = drag.original.source_out == null ? whole : drag.original.source_out;
    entry.source_out = clamp(round(base + delta), entry.source_in + MIN_CLIP, whole);
    return;
  }

  // Dragged onto another lane, it goes there.
  const lane = laneUnder(event);
  if (lane >= 0 && lane !== drag.track) {
    moveToTrack(drag, lane, now);
    return;
  }

  // Moving. A pinned clip slides in time; an unpinned one reorders, which is
  // what dragging means on a strip that has no gaps to slide into.
  if (entry.start != null || event.altKey) {
    if (entry.start == null) entry.start = placementOf(track, drag.entry);
    entry.start = Math.max(0, round((drag.original.start ?? entry.start) + delta));
    return;
  }
  reorderBy(track, drag, now);
}

/** Which lane the pointer is over, or -1. */
function laneUnder(event) {
  const node = document.elementFromPoint(event.clientX, event.clientY);
  const lane = node && node.closest(".tl-lane");
  return lane ? Number(lane.dataset.track) : -1;
}

/**
 * Move a clip to another lane, landing where the pointer is.
 *
 * It arrives pinned. A clip taken out of one sequence has no place in another
 * one's running order, and guessing a slot for it would move everything else on
 * that lane -- so it keeps the time it was dropped at, and can be unpinned from
 * the inspector to fall back into line.
 */
function moveToTrack(drag, toIndex, now) {
  const from = trackAt(drag.track);
  const to = trackAt(toIndex);
  if (!from || !to) return;

  const placed = layout(from).find((p) => p.index === drag.entry);
  const clip = from.entries[drag.entry];
  const grabbedAt = drag.at - (placed ? placed.start : 0);

  // Take the transition with it only if it would otherwise be left dangling.
  const before = from.entries[drag.entry - 1];
  const withJoin = isTransition(before) && !isClip(from.entries[drag.entry - 2]);
  from.entries.splice(withJoin ? drag.entry - 1 : drag.entry, withJoin ? 2 : 1);
  // A transition either side of the hole now joins nothing.
  for (let i = from.entries.length - 1; i >= 0; i--) {
    if (isTransition(from.entries[i]) &&
        (!isClip(from.entries[i - 1]) || !isClip(from.entries[i + 1]))) {
      from.entries.splice(i, 1);
    }
  }

  clip.start = Math.max(0, round(now - grabbedAt));
  to.entries.push(clip);

  drag.track = toIndex;
  drag.entry = to.entries.length - 1;
  drag.original = JSON.parse(JSON.stringify(clip));
  drag.startedAt = now;
  state.selected = { track: toIndex, entry: drag.entry };
}

function placementOf(track, entryIndex) {
  const found = layout(track).find((p) => p.index === entryIndex);
  return found ? found.start : 0;
}

/** Slide a clip past its neighbour once the pointer crosses that neighbour. */
function reorderBy(track, drag, now) {
  const placed = layout(track);
  const meIndex = placed.findIndex((p) => p.index === drag.entry);
  if (meIndex < 0) return;
  const target = placed.findIndex((p) => now >= p.start && now <= p.start + p.length);
  if (target < 0 || target === meIndex) return;
  moveClip(track, meIndex, target);
  // The entry index moved with it.
  const again = layout(track);
  state.selected.entry = again[target] ? again[target].index : drag.entry;
  drag.entry = state.selected.entry;
  drag.startedAt = now;
  drag.original = JSON.parse(JSON.stringify(track.entries[drag.entry]));
}

/**
 * Move one clip to another position among its track's clips.
 *
 * Transitions stay where they are rather than travelling with the clip: a
 * transition describes how two particular neighbours meet, so once the
 * neighbours change it belongs to the new pair, which is what an editor
 * expects when they drag a clip out of the middle of a sequence.
 */
export function moveClip(track, from, to) {
  const entries = track.entries;
  const clipPositions = entries
    .map((e, i) => (isClip(e) ? i : -1))
    .filter((i) => i >= 0);
  if (from === to || !clipPositions[from] === undefined) return;
  const clips = clipPositions.map((i) => entries[i]);
  const [moved] = clips.splice(from, 1);
  clips.splice(to, 0, moved);
  clipPositions.forEach((position, k) => {
    entries[position] = clips[k];
  });
}

export function removeEntry(track, index) {
  const entries = track.entries;
  const entry = entries[index];
  if (!entry) return;
  entries.splice(index, 1);
  // A transition with nothing on one side of it can no longer mean anything.
  for (let i = entries.length - 1; i >= 0; i--) {
    const previous = entries[i - 1];
    const next = entries[i + 1];
    if (isTransition(entries[i]) && (!isClip(previous) || !isClip(next))) {
      entries.splice(i, 1);
    }
  }
}

const round = (v) => Math.round(v * 1000) / 1000;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
