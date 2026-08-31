import { newTransition } from "./library.js";
import { clipsOf, isClip, newClip, newTrack, state, tracks } from "./state.js";
import { $, el, toast } from "./util.js";

// ---------------------------------------------------------------- lanes
//
// Adding, removing and ordering the tracks themselves. Kept apart from the
// timeline view because none of it is about drawing: a lane's existence and
// its place in the stack are properties of the project, and the view only
// reports them.
//
// Stacking order is list order, and the first video track is the *bottom*
// layer. The timeline draws them in the same order top-down, so "move up" here
// means "draw earlier, cover less", which is the opposite of the array index --
// worth stating once rather than discovering from the buttons.

let onChange = () => {};

export function bindLanes(handler) {
  onChange = handler || onChange;
}

export function addTrack(kind) {
  const count = tracks().filter((t) => t.kind === kind).length + 1;
  const name = kind === "video" ? `Video ${count}` : `Audio ${count}`;
  state.project.tracks.push(newTrack(kind, name));
  onChange();
}

export function removeTrack(index) {
  const track = tracks()[index];
  if (!track) return;
  const video = tracks().filter((t) => t.kind === "video");
  if (track.kind === "video" && video.length === 1) {
    toast("The last video track cannot be removed — there would be nothing to render.", true);
    return;
  }
  state.project.tracks.splice(index, 1);
  if (state.selected.track === index) state.selected = { track: -1, entry: -1 };
  else if (state.selected.track > index) state.selected.track -= 1;
  onChange();
}

export function moveTrack(index, by) {
  const list = state.project.tracks;
  const to = index + by;
  if (to < 0 || to >= list.length) return;
  [list[index], list[to]] = [list[to], list[index]];
  if (state.selected.track === index) state.selected.track = to;
  else if (state.selected.track === to) state.selected.track = index;
  onChange();
}

/**
 * Put clips on a track, joined by the project's default transition.
 *
 * The first clip added to an empty track gets no transition -- there is nothing
 * before it to come from -- which is the same rule the parser enforces.
 */
export function appendClips(track, paths) {
  const joinKind = state.project.defaults.join || "crossfade";
  paths.forEach((path) => {
    if (track.entries.length) {
      const joins = newTransition(joinKind);
      joins.duration = defaultDuration(joinKind);
      if (joinKind !== "cut") track.entries.push(joins);
    }
    track.entries.push(newClip(path));
  });
  onChange();
}

function defaultDuration(kind) {
  const d = state.project.defaults;
  if (kind === "cut") return 0;
  if (kind === "dip to black" || kind === "fade") return d.fade;
  if (kind === "audio overlap") return d.audio_overlap;
  return d.crossfade;
}

/** Insert a transition at a boundary that currently has none. */
export function insertTransition(track, beforeEntryIndex, kind) {
  const entries = track.entries;
  const previous = entries[beforeEntryIndex - 1];
  if (!isClip(entries[beforeEntryIndex]) || !isClip(previous)) return;
  const joins = newTransition(kind);
  joins.duration = defaultDuration(kind);
  entries.splice(beforeEntryIndex, 0, joins);
  onChange();
}

export function drawLaneControls() {
  const box = $("tl-tracks");
  if (!box) return;
  box.innerHTML = "";

  tracks().forEach((track, index) => {
    const row = el("div", "lane-row" + (state.selected.track === index ? " selected" : ""));
    row.appendChild(el("span", "kind", track.kind === "video" ? "V" : "A"));
    row.appendChild(el("span", "who", track.name));

    const up = el("button", "icon ghost", "↑");
    up.title = "Move down a layer (covered by more)";
    up.disabled = index === 0;
    up.addEventListener("click", () => moveTrack(index, -1));
    row.appendChild(up);

    const down = el("button", "icon ghost", "↓");
    down.title = "Move up a layer (covers more)";
    down.disabled = index === tracks().length - 1;
    down.addEventListener("click", () => moveTrack(index, 1));
    row.appendChild(down);

    const drop = el("button", "icon ghost danger", "×");
    drop.title = "Remove this track and everything on it";
    drop.addEventListener("click", () => {
      const count = clipsOf(track).length;
      if (count && !confirm(
        `Remove "${track.name}" and its ${count} clip(s)?`)) return;
      removeTrack(index);
    });
    row.appendChild(drop);
    box.appendChild(row);
  });
}
