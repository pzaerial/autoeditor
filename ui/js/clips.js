import { clipGain, hasEffect, setClipGain, toggleEffect } from "./library.js";
import { showPage } from "./pages.js";
import { openPicker } from "./picker.js";
import {
  audioFollowsPicture, audioJoinLabel, clipsOf, estimateLabel, layout,
  state, tracks, transitionLabel,
} from "./state.js";
import { $, el, fmt } from "./util.js";
import { makeReorderable, refreshAll, removeClip } from "./views.js";

// ---------------------------------------------------------------- clips page
//
// Every clip in the project, grouped by the track it sits on. The timeline is
// where an edit is shaped; this is where the same clips are worked through in
// bulk -- levels, silence, relinking a folder that moved -- which is awkward on
// a timeline and easy in a list.

export function renderClipTable() {
  const table = $("clip-table");
  if (!table) return;
  table.innerHTML = "";

  const total = tracks().reduce((n, t) => n + clipsOf(t).length, 0);
  if (!total) {
    table.appendChild(el("div", "empty-note", "No clips yet — add some files above."));
    $("clips-total").textContent = "";
    return;
  }

  const missing = tracks().reduce(
    (n, t) => n + clipsOf(t).filter((c) => c.missing).length, 0);
  if (missing) {
    const banner = el("div", "clip-missing-note");
    banner.textContent =
      `${missing} clip(s) point at files that are not there. Relink one and the ` +
      "others in the same folder can follow.";
    table.appendChild(banner);
  }

  tracks().forEach((track, trackIndex) => {
    const clips = clipsOf(track);
    if (!clips.length) return;

    const head = el("div", "clip-track-head");
    head.textContent = `${track.kind === "video" ? "Video" : "Audio"}: ${track.name}`;
    if (track.muted) head.appendChild(el("span", "badge", "muted"));
    if (track.hidden) head.appendChild(el("span", "badge", "hidden"));
    table.appendChild(head);

    const columns = el("div", "clip-head");
    ["", "#", "Clip", "Joins from previous", "Volume & silence", "On screen", ""]
      .forEach((title) => columns.appendChild(el("div", null, title)));
    table.appendChild(columns);

    layout(track).forEach((placed, i) => {
      table.appendChild(clipRow(track, trackIndex, placed, i));
    });
  });

  $("clips-total").textContent = `${total} clip(s) · ${estimateLabel()}`;
}

function clipRow(track, trackIndex, placed, i) {
  const clip = placed.clip;
  const selected = state.selected.track === trackIndex &&
    state.selected.entry === placed.index;
  const row = el("div", "clip-row" + (clip.missing ? " missing" : "") +
    (selected ? " selected" : ""));

  row.appendChild(el("div", "grip", "≡"));
  row.appendChild(el("div", "idx", String(i + 1)));

  const name = el("div", "who");
  name.appendChild(el("div", "title", clip.label));
  name.appendChild(el("div", "path", clip.path));
  name.addEventListener("click", () => {
    state.selected = { track: trackIndex, entry: placed.index };
    showPage("edit");
    refreshAll();
  });
  row.appendChild(name);

  // How it meets the clip before it.
  const join = el("div", "join");
  if (!placed.before && i === 0) {
    join.appendChild(el("span", "hint", "first on this track"));
  } else {
    join.appendChild(el("span", "mono", transitionLabel(placed.before)));
    if (placed.before && !audioFollowsPicture(placed.before)) {
      join.appendChild(el("span", "hint", audioJoinLabel(placed.before)));
    }
  }
  row.appendChild(join);

  const audio = el("div", "audio-cell");
  const gain = el("input", "mono jd");
  gain.type = "number";
  gain.step = "0.5";
  gain.title =
    "How much this clip's sound is raised or lowered, in dB. " +
    "Audio auto-balance fills this in for you; change it and your number stands.";
  gain.value = clipGain(clip);
  gain.addEventListener("change", () => {
    setClipGain(clip, parseFloat(gain.value) || 0);
    refreshAll();
  });
  audio.appendChild(gain);
  audio.appendChild(el("span", "unit", "dB"));

  const trim = el("label", "check");
  const box = el("input");
  box.type = "checkbox";
  box.checked = hasEffect(clip, "trim silence");
  box.addEventListener("change", () => {
    toggleEffect(clip, "trim silence", box.checked);
    refreshAll();
  });
  trim.title = "Cut the dead air out of this clip";
  trim.appendChild(box);
  trim.appendChild(el("span", null, "trim silence"));
  audio.appendChild(trim);
  row.appendChild(audio);

  const when = el("div", "kept");
  when.appendChild(el("div", null, fmt(placed.length)));
  const notes = [];
  notes.push(`at ${fmt(placed.start)}`);
  if (clip.start != null) notes.push("pinned");
  if (clip.missing) notes.push("missing");
  when.appendChild(el("div", "hint", notes.join(" · ")));
  row.appendChild(when);

  const remove = el("button", "remove", "×");
  remove.title = "Remove clip";
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    removeClip(trackIndex, placed.index);
  });
  row.appendChild(remove);

  makeReorderable(row, trackIndex, i, refreshAll);
  return row;
}

const add = $("clips-add");
if (add) add.addEventListener("click", () => openPicker());
