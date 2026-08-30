import { currentDuration } from "./editor.js";
import { playheadInView, updatePlayhead } from "./preview.js";
import { drawRegions, playheadTime } from "./regions.js";
import { MIN_SPAN, state } from "./state.js";
import { $, clamp, el, fmtTick } from "./util.js";

// ---------------------------------------------------------------- edit: zoom
//
// Everything drawn on the scrubber -- filmstrip, regions, ruler, playhead --
// is positioned against `view`, the window of the clip currently on screen.
// The overview strip below shows where that window sits in the whole clip.

export function view() {
  const duration = currentDuration();
  if (!state.view || !duration) return { start: 0, end: duration, span: duration };
  const start = clamp(state.view.start, 0, Math.max(0, duration - MIN_SPAN));
  const end = clamp(state.view.end, start + Math.min(MIN_SPAN, duration), duration);
  return { start, end, span: end - start };
}

function maxZoom() {
  const duration = currentDuration();
  return duration > MIN_SPAN ? duration / MIN_SPAN : 1;
}

export function resetView() {
  state.view = null;
  refreshScrubber();
}

/** Show `span` seconds, holding `anchor` at the same place on screen. */
export function setView(span, anchor, anchorRatio) {
  const duration = currentDuration();
  if (!duration) return;
  span = clamp(span, Math.min(MIN_SPAN, duration), duration);
  let start = clamp(anchor - span * anchorRatio, 0, duration - span);
  state.view = span >= duration - 1e-6 ? null : { start, end: start + span };
  refreshScrubber();
}

export function zoomBy(factor, anchor, anchorRatio) {
  const v = view();
  setView(v.span / factor, anchor === undefined ? v.start + v.span / 2 : anchor,
          anchorRatio === undefined ? 0.5 : anchorRatio);
}

export function panTo(start) {
  const duration = currentDuration();
  const v = view();
  if (v.span >= duration) return;
  state.view = { start: clamp(start, 0, duration - v.span) };
  state.view.end = state.view.start + v.span;
  refreshScrubber();
}

/** Keep the playhead on screen while playing a zoomed-in clip. */
export function followPlayhead(at) {
  const v = view();
  const duration = currentDuration();
  if (v.span >= duration) return;
  if (at < v.start || at > v.end) panTo(at - v.span * 0.25);
}

export function refreshScrubber() {
  drawRuler();
  drawRegions();
  drawOverview();
  scheduleFilmstrip();
  syncZoomControls();
  updatePlayhead(playheadTime());
}

function syncZoomControls() {
  const duration = currentDuration();
  const v = view();
  const zoom = v.span > 0 ? duration / v.span : 1;
  const top = maxZoom();
  const slider = $("zoom-slider");
  slider.value = top > 1 ? Math.round((Math.log(zoom) / Math.log(top)) * 100) : 0;
  $("zoom-readout").textContent = duration
    ? `${zoom.toFixed(zoom < 10 ? 1 : 0)}×   ${fmtTick(v.start, v.span)} – ${fmtTick(v.end, v.span)}`
    : "";
  $("zoom-out").disabled = zoom <= 1.0001;
  $("zoom-in").disabled = zoom >= top - 1e-6;
  $("zoom-reset").disabled = zoom <= 1.0001;
}

$("zoom-slider").addEventListener("input", () => {
  const duration = currentDuration();
  if (!duration) return;
  const top = maxZoom();
  const zoom = Math.pow(top, parseFloat($("zoom-slider").value) / 100);
  const v = view();
  setView(duration / zoom, playheadInView() ? playheadTime() : v.start + v.span / 2, 0.5);
});

// The wheel over the slider nudges it, matching the wheel over the scrubber.
$("zoom-slider").addEventListener("wheel", (event) => {
  event.preventDefault();
  zoomBy(event.deltaY < 0 ? 1.25 : 1 / 1.25);
}, { passive: false });

$("zoom-in").addEventListener("click", () => zoomBy(2));
$("zoom-out").addEventListener("click", () => zoomBy(0.5));
$("zoom-reset").addEventListener("click", resetView);

// Rolling over the timeline zooms around the pointer, so the frame under the
// cursor stays put -- shift rolls sideways instead.
$("scrubber").addEventListener("wheel", (event) => {
  if (!currentDuration()) return;
  event.preventDefault();
  const box = $("scrubber").getBoundingClientRect();
  const ratio = clamp((event.clientX - box.left) / box.width, 0, 1);
  const v = view();
  if (event.shiftKey) {
    panTo(v.start + v.span * 0.2 * Math.sign(event.deltaY));
  } else {
    setView(v.span / (event.deltaY < 0 ? 1.25 : 1 / 1.25), v.start + v.span * ratio, ratio);
  }
}, { passive: false });

// -- the overview strip: pan by dragging the window, zoom by dragging an edge

export function drawOverview() {
  const duration = currentDuration();
  const v = view();
  const window = $("overview-window");
  const pct = (t) => (duration ? (t / duration) * 100 : 0);

  window.style.left = pct(v.start) + "%";
  window.style.width = Math.max(0.6, pct(v.span)) + "%";
  $("overview").classList.toggle("zoomed", v.span < duration - 1e-6);
  $("overview-playhead").style.left = pct(playheadTime()) + "%";

  const layer = $("overview-regions");
  layer.innerHTML = "";
  const clip = state.project.clips[state.selected];
  (clip ? clip.regions || [] : []).forEach((region) => {
    const band = el("div", "ov-region");
    band.style.left = pct(region.start) + "%";
    band.style.width = Math.max(0.3, pct(region.end - region.start)) + "%";
    layer.appendChild(band);
  });
}

$("overview").addEventListener("pointerdown", (event) => {
  const duration = currentDuration();
  if (!duration) return;
  event.preventDefault();
  $("overview").setPointerCapture(event.pointerId);

  const box = $("overview").getBoundingClientRect();
  const at = (clientX) => clamp((clientX - box.left) / box.width, 0, 1) * duration;
  const v = view();
  const handle = event.target.closest(".ov-handle");

  if (handle) {
    const edge = handle.classList.contains("left") ? "start" : "end";
    state.overviewDrag = { kind: "resize", edge };
  } else if (event.target.closest(".overview-window")) {
    state.overviewDrag = { kind: "pan", grab: at(event.clientX) - v.start };
  } else {
    // A click on empty track centres the window there.
    panTo(at(event.clientX) - v.span / 2);
    state.overviewDrag = { kind: "pan", grab: v.span / 2 };
  }
  state.overviewDrag.at = at;
});

$("overview").addEventListener("pointermove", (event) => {
  const drag = state.overviewDrag;
  if (!drag) return;
  const at = drag.at(event.clientX);
  const v = view();
  if (drag.kind === "pan") {
    panTo(at - drag.grab);
  } else if (drag.edge === "start") {
    const end = v.end;
    setView(Math.max(MIN_SPAN, end - at), Math.min(at, end - MIN_SPAN), 0);
  } else {
    setView(Math.max(MIN_SPAN, at - v.start), v.start, 0);
  }
});

$("overview").addEventListener("pointerup", (event) => {
  if (!state.overviewDrag) return;
  state.overviewDrag = null;
  $("overview").releasePointerCapture(event.pointerId);
});

// ---------------------------------------------------------------- edit: filmstrip & ruler

let filmstripTimer = null;
let filmstripKey = "";

function scheduleFilmstrip() {
  clearTimeout(filmstripTimer);
  filmstripTimer = setTimeout(buildFilmstrip, 140);
}

function buildFilmstrip() {
  const clip = state.project.clips[state.selected];
  const strip = $("filmstrip");
  const duration = currentDuration();
  if (!clip || !duration || clip.missing) {
    strip.innerHTML = "";
    filmstripKey = "";
    return;
  }
  const v = view();
  const key = `${clip.path}|${v.start.toFixed(2)}|${v.span.toFixed(2)}`;
  if (key === filmstripKey) return;
  filmstripKey = key;

  strip.innerHTML = "";
  const count = 12;
  for (let i = 0; i < count; i++) {
    const at = v.start + (v.span * (i + 0.5)) / count;
    const img = new Image();
    img.loading = "lazy";
    img.draggable = false;
    img.src = `/thumb?path=${encodeURIComponent(clip.path)}&t=${at.toFixed(2)}`;
    img.onerror = () => img.remove();
    strip.appendChild(img);
  }
}

function drawRuler() {
  const ruler = $("ruler");
  ruler.innerHTML = "";
  const v = view();
  for (let i = 0; i <= 4; i++) {
    ruler.appendChild(el("span", null, fmtTick(v.start + (v.span * i) / 4, v.span)));
  }
}
