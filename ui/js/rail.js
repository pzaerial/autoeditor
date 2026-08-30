import { selectClip } from "./editor.js";
import { audioFollowsPicture, audioJoinLabel, estimateLabel, joinLabel, keptDuration, state } from "./state.js";
import { $, el, fmt } from "./util.js";
import { makeReorderable, refreshAll, removeClip } from "./views.js";

// ---------------------------------------------------------------- edit: clip rail

export function renderClipList() {
  const list = $("clip-list");
  list.innerHTML = "";

  state.project.clips.forEach((clip, i) => {
    const item = el("li", "clip-item" + (i === state.selected ? " selected" : "") + (clip.missing ? " missing" : ""));
    item.dataset.index = i;

    item.appendChild(el("span", "grip", "≡"));

    const body = el("div", "body");
    body.appendChild(el("div", "title", `${i + 1}. ${clip.label}`));
    const bits = [];
    if (i > 0) bits.push(joinLabel(clip));
    bits.push(fmt(keptDuration(clip)));
    if (clip.regions && clip.regions.length) bits.push(`${clip.regions.length} region(s)`);
    if (clip.trim_silence) bits.push("silence");
    if (clip.audio_gain_db) bits.push(`${clip.audio_gain_db > 0 ? "+" : ""}${clip.audio_gain_db}dB`);
    if (i > 0 && !audioFollowsPicture(clip)) bits.push(audioJoinLabel(clip));
    if (clip.missing) bits.push("missing");
    body.appendChild(el("div", "sub", bits.join(" · ")));
    item.appendChild(body);

    const remove = el("button", "remove", "×");
    remove.title = "Remove clip";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      removeClip(i);
    });
    item.appendChild(remove);

    item.addEventListener("click", () => selectClip(i));
    makeReorderable(item, i, () => {
      refreshAll();
      selectClip(state.selected);
    });

    list.appendChild(item);
  });

  $("rail-total").textContent = state.project.clips.length
    ? `${state.project.clips.length} clips · ${estimateLabel()}`
    : "No clips yet — add some on the Clips page.";

  if (state.selected < 0 || !state.project.clips.length) {
    $("clip-editor").classList.add("hidden");
    $("no-clip").classList.remove("hidden");
  }
}
