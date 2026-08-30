import { selectClip, syncGainControls } from "./editor.js";
import { showPage } from "./pages.js";
import { openPicker } from "./picker.js";
import { balanceDb } from "./preview.js";
import { joinDuration, estimateLabel, keptDuration, state } from "./state.js";
import { $, el, fmt } from "./util.js";
import { makeReorderable, refreshAll, removeClip } from "./views.js";

// ---------------------------------------------------------------- clips page

export function renderClipTable() {
  const table = $("clip-table");
  table.innerHTML = "";

  if (!state.project.clips.length) {
    table.appendChild(el("div", "empty-note", "No clips yet — add some files above."));
    $("clips-total").textContent = "";
    return;
  }

  const missing = state.project.clips.filter((c) => c.missing).length;
  if (missing) {
    const banner = el("div", "clip-missing-note");
    banner.textContent =
      `${missing} clip(s) point at files that are not there. Relink one and the ` +
      "others in the same folder can follow.";
    table.appendChild(banner);
  }

  const head = el("div", "clip-head");
  ["", "#", "Clip", "Joins from previous", "Audio", "Kept", ""].forEach((title) =>
    head.appendChild(el("div", null, title))
  );
  table.appendChild(head);

  state.project.clips.forEach((clip, i) => {
    const row = el("div", "clip-row" + (clip.missing ? " missing" : "") +
      (i === state.selected ? " selected" : ""));

    row.appendChild(el("div", "grip", "≡"));
    row.appendChild(el("div", "idx", String(i + 1)));

    const name = el("div", "who");
    name.appendChild(el("div", "title", clip.label));
    name.appendChild(el("div", "path", clip.path));
    name.addEventListener("click", () => {
      selectClip(i);
      showPage("edit");
    });
    row.appendChild(name);

    const join = el("div", "join-cell");
    if (i === 0) {
      join.appendChild(el("span", "hint", "first clip"));
    } else {
      const pick = el("select");
      ["crossfade", "cut", "fade", "audio overlap"].forEach((value) => {
        const option = el("option", null, value);
        option.value = value;
        pick.appendChild(option);
      });
      pick.value = clip.join;
      pick.addEventListener("change", () => {
        clip.join = pick.value;
        const d = state.project.defaults;
        clip.join_duration = joinDuration(clip.join, d);
        refreshAll();
      });
      join.appendChild(pick);

      const secs = el("input", "mono jd");
      secs.type = "number";
      secs.step = "0.1";
      secs.min = "0";
      secs.value = clip.join_duration;
      secs.disabled = clip.join === "cut";
      secs.addEventListener("change", () => {
        clip.join_duration = Math.max(0, parseFloat(secs.value) || 0);
        refreshAll();
      });
      join.appendChild(secs);
    }
    row.appendChild(join);

    const audio = el("div", "audio-cell");
    const gain = el("input", "mono jd");
    gain.type = "number";
    gain.step = "0.5";
    gain.title = "Volume trim for this clip, in dB";
    gain.value = clip.audio_gain_db || 0;
    gain.addEventListener("change", () => {
      clip.audio_gain_db = parseFloat(gain.value) || 0;
      if (i === state.selected) syncGainControls();
      refreshAll();
    });
    audio.appendChild(gain);
    audio.appendChild(el("span", "unit", "dB"));

    // What levelling worked out, so the Clips page reflects it as soon as it runs.
    const levelled = balanceDb(clip);
    if (levelled) {
      const tag = el("span", "levelled", `${levelled > 0 ? "+" : ""}${levelled}`);
      tag.title = "Set by Balance clip levels; adds to the trim on the left";
      audio.appendChild(tag);
    }

    const trim = el("label", "check");
    const box = el("input");
    box.type = "checkbox";
    box.checked = !!clip.trim_silence;
    box.addEventListener("change", () => {
      clip.trim_silence = box.checked;
      refreshAll();
    });
    trim.appendChild(box);
    trim.appendChild(el("span", null, "trim"));
    audio.appendChild(trim);
    row.appendChild(audio);

    const kept = el("div", "kept");
    kept.appendChild(el("div", null, fmt(keptDuration(clip))));
    const notes = [];
    if (clip.regions && clip.regions.length) notes.push(`${clip.regions.length} region(s)`);
    if (clip.missing) notes.push("missing");
    kept.appendChild(el("div", "hint", notes.join(" · ")));
    row.appendChild(kept);

    const actions = el("div", "actions");
    const relink = el("button", "ghost small" + (clip.missing ? " urgent" : ""), "Relink");
    relink.title = "Point this clip at a different file";
    relink.addEventListener("click", (event) => {
      event.stopPropagation();
      openPicker(i);
    });
    actions.appendChild(relink);

    const remove = el("button", "remove", "×");
    remove.title = "Remove clip";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      removeClip(i);
    });
    actions.appendChild(remove);
    row.appendChild(actions);

    makeReorderable(row, i, () => refreshAll());
    table.appendChild(row);
  });

  $("clips-total").textContent =
    `${state.project.clips.length} clips · ${estimateLabel()}`;
}
