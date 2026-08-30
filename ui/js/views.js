import { renderClipTable } from "./clips.js";
import { selectClip } from "./editor.js";
import { renderClipList } from "./rail.js";
import { audioFollowsPicture, audioJoinLabel, joinLabel, keptDuration, state } from "./state.js";
import { $, el, fmt, toast } from "./util.js";

// ---------------------------------------------------------------- shared rendering

export function miniTimeline(target) {
  target.innerHTML = "";
  if (!state.project.clips.length) {
    target.appendChild(el("div", "empty-note", "No clips yet."));
    return;
  }
  state.project.clips.forEach((clip, i) => {
    const row = el("div", "mini-row" + (clip.missing ? " missing" : ""));
    row.appendChild(el("div", "idx", String(i + 1)));
    row.appendChild(el("div", "name", clip.label));
    const tags = [];
    if (i > 0) tags.push(joinLabel(clip));
    if (clip.trim_silence) tags.push("trim silence");
    if (clip.audio_gain_db) tags.push(`${clip.audio_gain_db > 0 ? "+" : ""}${clip.audio_gain_db} dB`);
    if (i > 0 && !audioFollowsPicture(clip)) tags.push(audioJoinLabel(clip));
    if (clip.regions && clip.regions.length) tags.push(`${clip.regions.length} region(s)`);
    if (clip.missing) tags.push("MISSING");
    tags.push(fmt(keptDuration(clip)));
    row.appendChild(el("div", "tag", tags.join("  ·  ")));
    target.appendChild(row);
  });
}

/** Wire a row so it can be dragged to a new position in the timeline. */
export function makeReorderable(node, index, onDone) {
  node.draggable = true;
  node.addEventListener("dragstart", (e) => {
    node.classList.add("dragging");
    e.dataTransfer.setData("text/plain", String(index));
    e.dataTransfer.effectAllowed = "move";
  });
  node.addEventListener("dragend", () => {
    node.classList.remove("dragging");
    document.querySelectorAll(".drop-target").forEach((n) => n.classList.remove("drop-target"));
  });
  node.addEventListener("dragover", (e) => {
    e.preventDefault();
    node.classList.add("drop-target");
  });
  node.addEventListener("dragleave", () => node.classList.remove("drop-target"));
  node.addEventListener("drop", (e) => {
    e.preventDefault();
    node.classList.remove("drop-target");
    const from = parseInt(e.dataTransfer.getData("text/plain"), 10);
    if (isNaN(from) || from === index) return;
    const [moved] = state.project.clips.splice(from, 1);
    state.project.clips.splice(index, 0, moved);
    state.selected = index;
    onDone();
  });
}

/** Remove a clip, keeping it for one undo -- regions are slow to rebuild. */
export function removeClip(index) {
  const clip = state.project.clips[index];
  if (!clip) return;
  state.project.clips.splice(index, 1);
  state.removed = { clip, index };
  if (state.selected >= state.project.clips.length) {
    state.selected = state.project.clips.length - 1;
  }
  refreshAll();
  selectClip(state.selected);
  toast(`Removed ${clip.label} — Ctrl+Z to put it back.`);
}

export function undoRemove() {
  const last = state.removed;
  if (!last) return false;
  state.removed = null;
  const at = Math.min(last.index, state.project.clips.length);
  state.project.clips.splice(at, 0, last.clip);
  refreshAll();
  selectClip(at);
  toast(`${last.clip.label} is back.`);
  return true;
}

document.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
  if (undoRemove()) event.preventDefault();
});

export function refreshAll() {
  $("setup-count").textContent = state.project.clips.length;
  renderClipTable();
  renderClipList();
  $("project-title").textContent = state.project.title || "";
}
