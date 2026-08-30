import { renderClipTable } from "./clips.js";
import { stopPlayback } from "./preview.js";
import { renderClipList } from "./rail.js";
import { renderSummary } from "./render.js";
import { state } from "./state.js";
import { $ } from "./util.js";

// ---------------------------------------------------------------- pages

export function showPage(name) {
  // Leaving the Edit page must silence the preview -- audio playing under a
  // page you are not looking at is never what you meant.
  if (state.page === "edit" && name !== "edit") stopPlayback();
  state.page = name;

  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  $("page-" + name).classList.add("active");
  document.querySelectorAll(".step").forEach((b) =>
    b.classList.toggle("active", b.dataset.page === name)
  );
  if (name === "clips") renderClipTable();
  if (name === "edit") renderClipList();
  if (name === "render") renderSummary();
}

document.querySelectorAll(".step").forEach((button) =>
  button.addEventListener("click", () => showPage(button.dataset.page))
);

// A hidden tab, a minimised window or a lock screen stops the preview too.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPlayback();
});
