import { renderClipTable } from "./clips.js";
import {
  audioFollowsPicture, audioJoinLabel, clipsOf, isClip, layout,
  projectDuration, state, trackAt, tracks, transitionLabel,
} from "./state.js";
import { drawInspector } from "./inspector.js";
import { refreshIfEmpty } from "./preview.js";
import { drawTimeline } from "./timeline.js";
import { drawLaneControls } from "./tracklanes.js";
import { $, el, fmt, toast } from "./util.js";

// ---------------------------------------------------------------- shared rendering

/** The whole edit as text, for the Render page's summary. */
export function miniTimeline(target) {
  target.innerHTML = "";
  if (!tracks().some((t) => clipsOf(t).length)) {
    target.appendChild(el("div", "empty-note", "No clips yet."));
    return;
  }
  tracks().forEach((track) => {
    const clips = clipsOf(track);
    if (!clips.length) return;
    const head = el("div", "mini-track");
    head.textContent = `${track.kind === "video" ? "Video" : "Audio"}: ${track.name}` +
      (track.muted ? "  (muted)" : "") + (track.hidden ? "  (hidden)" : "") +
      (track.gain_db ? `  ${track.gain_db > 0 ? "+" : ""}${track.gain_db} dB` : "");
    target.appendChild(head);

    layout(track).forEach((placed, i) => {
      const row = el("div", "mini-row" + (placed.clip.missing ? " missing" : ""));
      row.appendChild(el("div", "idx", String(i + 1)));
      row.appendChild(el("div", "name", placed.clip.label));
      const tags = [];
      if (placed.before) {
        tags.push(transitionLabel(placed.before));
        if (!audioFollowsPicture(placed.before)) tags.push(audioJoinLabel(placed.before));
      }
      (placed.clip.effects || []).forEach((e) =>
        tags.push(e.name === "volume"
          ? `${e.params.db > 0 ? "+" : ""}${e.params.db} dB` : e.name));
      if (placed.clip.start != null) tags.push(`at ${fmt(placed.clip.start)}`);
      if (placed.clip.missing) tags.push("MISSING");
      tags.push(fmt(placed.length));
      row.appendChild(el("div", "tag", tags.join("  ·  ")));
      target.appendChild(row);
    });
  });
}

/** Wire a row so it can be dragged to a new position among its track's clips. */
export function makeReorderable(node, track, index, onDone) {
  node.draggable = true;
  node.addEventListener("dragstart", (e) => {
    node.classList.add("dragging");
    e.dataTransfer.setData("text/plain", JSON.stringify({ track, index }));
    e.dataTransfer.effectAllowed = "move";
  });
  node.addEventListener("dragend", () => {
    node.classList.remove("dragging");
    document.querySelectorAll(".drop-target")
      .forEach((n) => n.classList.remove("drop-target"));
  });
  node.addEventListener("dragover", (e) => {
    e.preventDefault();
    node.classList.add("drop-target");
  });
  node.addEventListener("dragleave", () => node.classList.remove("drop-target"));
  node.addEventListener("drop", (e) => {
    e.preventDefault();
    node.classList.remove("drop-target");
    let from;
    try {
      from = JSON.parse(e.dataTransfer.getData("text/plain"));
    } catch {
      return;
    }
    if (from.track !== track || from.index === index) return;
    const lane = trackAt(track);
    const positions = lane.entries
      .map((entry, i) => (isClip(entry) ? i : -1))
      .filter((i) => i >= 0);
    const clips = positions.map((i) => lane.entries[i]);
    const [moved] = clips.splice(from.index, 1);
    clips.splice(index, 0, moved);
    positions.forEach((position, k) => (lane.entries[position] = clips[k]));
    onDone();
  });
}

// ---------------------------------------------------------------- removal

/** Remove a clip, keeping it for one undo. */
export function removeClip(trackIndex, entryIndex) {
  const track = trackAt(trackIndex);
  const clip = track && track.entries[entryIndex];
  if (!clip) return;
  // Keep the transition that came with it, so undo restores the whole join
  // rather than dropping the clip back in as a hard cut.
  const before = track.entries[entryIndex - 1];
  const withJoin = before && !isClip(before);
  const at = withJoin ? entryIndex - 1 : entryIndex;
  const cut = track.entries.splice(at, withJoin ? 2 : 1);
  state.removed = { track: trackIndex, index: at, entries: cut, label: clip.label };
  state.selected = { track: -1, entry: -1 };
  refreshAll();
  toast(`Removed ${clip.label} — Ctrl+Z to put it back.`);
}

export function undoRemove() {
  const last = state.removed;
  if (!last) return false;
  state.removed = null;
  const track = trackAt(last.track);
  if (!track) return false;
  const at = Math.min(last.index, track.entries.length);
  track.entries.splice(at, 0, ...last.entries);
  refreshAll();
  toast(`${last.label} is back.`);
  return true;
}

document.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
  if (undoRemove()) event.preventDefault();
});

// ---------------------------------------------------------------- refresh

export function clipCount() {
  return tracks().reduce((n, t) => n + clipsOf(t).length, 0);
}

export function refreshAll() {
  $("setup-count").textContent = clipCount();
  // The transport's total comes from the timeline, so it has to be redrawn
  // whenever the edit changes length -- which is most edits.
  const readout = $("time-readout");
  if (readout) {
    readout.textContent =
      `${fmt(state.playheadAt || 0)} / ${fmt(projectDuration())}`;
  }
  renderClipTable();
  drawTimeline();
  drawLaneControls();
  drawInspector();
  refreshIfEmpty();
  $("project-title").textContent = state.project.title || "";
}
