import { currentDuration, syncJoinAudio } from "./editor.js";
import { playRegion, seekTo, settleSeek, stopPlayback, video } from "./preview.js";
import { MIN_REGION, MIN_SPAN, keptDuration, newRegion, regionBounds, state } from "./state.js";
import { $, clamp, el, fmt, fmtPrecise, parseTime, toast } from "./util.js";
import { refreshAll, removeClip } from "./views.js";
import { drawOverview, resetView, setView, view, zoomBy } from "./zoom.js";

// ---------------------------------------------------------------- edit: regions

export function drawRegions() {
  const clip = state.project.clips[state.selected];
  const layer = $("region-layer");
  const rows = $("region-rows");
  layer.innerHTML = "";
  rows.innerHTML = "";
  if (!clip) return;

  const duration = currentDuration();
  const v = view();
  const regions = clip.regions || [];
  $("region-count").textContent = regions.length;

  regions.forEach((region, i) => {
    // Bands are positioned against the visible window; the scrubber clips the
    // overflow, so a region running off either edge draws correctly.
    if (v.span > 0 && region.end > v.start && region.start < v.end) {
      const band = el("div", "region" + (i === state.activeRegion ? " active" : ""));
      band.style.left = ((region.start - v.start) / v.span) * 100 + "%";
      band.style.width = ((region.end - region.start) / v.span) * 100 + "%";
      band.dataset.index = i;
      band.appendChild(el("div", "label", `${i + 1}  ${fmt(region.end - region.start)}`));
      band.appendChild(el("div", "handle left"));
      band.appendChild(el("div", "handle right"));
      layer.appendChild(band);
    }

    const row = el("div", "region-row" + (i === state.activeRegion ? " active" : ""));
    row.addEventListener("click", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      selectRegion(i);
    });

    row.appendChild(el("span", "rid", String(i + 1)));

    // How this region attaches to the one before it inside the same clip.
    if (i > 0) {
      const join = el("select", "join-pick");
      [["cut", "cut"], ["crossfade", "crossfade"], ["fade", "fade"]].forEach(([value, text]) => {
        const option = el("option", null, text);
        option.value = value;
        join.appendChild(option);
      });
      join.value = region.join || "cut";
      join.addEventListener("change", () => {
        region.join = join.value;
        const d = state.project.defaults;
        region.join_duration =
          region.join === "cut" ? 0 : region.join === "fade" ? d.fade : d.crossfade;
        drawRegions();
        refreshAll();
      });
      row.appendChild(join);

      const secs = el("input", "mono jd");
      secs.type = "number";
      secs.step = "0.1";
      secs.min = "0";
      secs.value = region.join_duration || 0;
      secs.disabled = (region.join || "cut") === "cut";
      secs.title = "Join length in seconds";
      secs.addEventListener("change", () => {
        region.join_duration = Math.max(0, parseFloat(secs.value) || 0);
        if (region.join_duration === 0) region.join = "cut";
        drawRegions();
        refreshAll();
      });
      row.appendChild(secs);
    } else {
      row.appendChild(el("span", "join-spacer"));
    }

    const from = el("input", "mono");
    from.type = "text";
    from.value = fmtPrecise(region.start);
    from.addEventListener("change", () => editRegion(i, "start", from.value));
    row.appendChild(from);

    row.appendChild(el("span", "arrow", "→"));

    const to = el("input", "mono");
    to.type = "text";
    to.value = fmtPrecise(region.end);
    to.addEventListener("change", () => editRegion(i, "end", to.value));
    row.appendChild(to);

    const play = el("button", "ghost", "Play");
    play.addEventListener("click", () => playRegion(region));
    row.appendChild(play);

    const zoom = el("button", "ghost", "Zoom");
    zoom.title = "Fit this region on the timeline";
    zoom.addEventListener("click", () => {
      const span = Math.max(MIN_SPAN, (region.end - region.start) * 1.4);
      setView(span, (region.start + region.end) / 2, 0.5);
    });
    row.appendChild(zoom);

    const drop = el("button", "ghost", "×");
    drop.title = "Delete region";
    drop.addEventListener("click", () => {
      clip.regions.splice(i, 1);
      if (clip.regions.length) clip.regions[0].join = "cut";
      state.activeRegion = Math.min(state.activeRegion, clip.regions.length - 1);
      drawRegions();
      drawOverview();
      refreshAll();
    });
    row.appendChild(drop);

    row.appendChild(el("span", "dur", fmt(region.end - region.start)));
    rows.appendChild(row);
  });

  syncJoinAudio();

  const hint = $("region-hint");
  hint.textContent = regions.length
    ? `Keeping ${fmt(keptDuration(clip))} of ${fmt(duration)}.`
    : `No regions — the whole clip plays (${fmt(duration)}).`;
}

export function selectRegion(index) {
  state.activeRegion = index;
  drawRegions();
}

export function editRegion(index, field, text) {
  const clip = state.project.clips[state.selected];
  const seconds = parseTime(text);
  if (seconds === null) {
    toast("Use a timecode like 1:23 or 83.5", true);
    drawRegions();
    return;
  }
  const duration = currentDuration();
  const region = clip.regions[index];
  const [low, high] = regionBounds(clip.regions, index, duration);

  if (field === "start") {
    region.start = Math.max(low, Math.min(seconds, region.end - MIN_REGION));
  } else {
    region.end = Math.min(high, Math.max(seconds, region.start + MIN_REGION));
  }
  drawRegions();
  drawOverview();
  refreshAll();
}

// ---------------------------------------------------------------- edit: scrubber gestures
//
// One pointer gesture handler covers scrubbing, creating, moving and resizing,
// so the video seeks live throughout and the filmstrip never gets dragged.

function positionToTime(clientX) {
  const box = $("scrubber").getBoundingClientRect();
  const v = view();
  const ratio = clamp((clientX - box.left) / box.width, 0, 1);
  return v.start + ratio * v.span;
}

export function setMarking(on) {
  state.marking = on;
  state.markStart = null;
  $("scrubber").classList.toggle("marking", on);
  $("mark-region").classList.toggle("primary", on);
  $("mark-hint").textContent = on
    ? "Marking: drag across the timeline, or press [ and ] at the playhead. Esc to cancel."
    : "";
}

$("mark-region").addEventListener("click", () => setMarking(!state.marking));

$("scrubber").addEventListener("pointerdown", (event) => {
  const clip = state.project.clips[state.selected];
  if (!clip || !currentDuration()) return;
  event.preventDefault();
  $("scrubber").setPointerCapture(event.pointerId);
  stopPlayback();

  const at = positionToTime(event.clientX);
  const handle = event.target.closest(".handle");
  const band = event.target.closest(".region");

  if (handle && band) {
    const index = Number(band.dataset.index);
    selectRegion(index);
    state.drag = { kind: "resize", index, edge: handle.classList.contains("left") ? "start" : "end" };
  } else if (band) {
    const index = Number(band.dataset.index);
    selectRegion(index);
    const region = clip.regions[index];
    state.drag = { kind: "move", index, grab: at - region.start, span: region.end - region.start };
  } else if (state.marking) {
    clip.regions = clip.regions || [];
    clip.regions.push(newRegion(at, at + MIN_REGION));
    clip.regions.sort((a, b) => a.start - b.start);
    const index = clip.regions.findIndex((r) => r.start === at);
    state.drag = { kind: "create", index, anchor: at };
    selectRegion(index);
  } else {
    state.drag = { kind: "scrub" };
  }

  handleDrag(event);
});

$("scrubber").addEventListener("pointermove", (event) => {
  if (state.drag) handleDrag(event);
});

$("scrubber").addEventListener("pointerup", (event) => {
  if (!state.drag) return;
  const kind = state.drag.kind;
  const clip = state.project.clips[state.selected];
  state.drag = null;
  $("scrubber").releasePointerCapture(event.pointerId);

  if (kind === "create") {
    const region = clip.regions[state.activeRegion];
    // A click rather than a drag leaves a sliver; drop it instead of keeping it.
    if (region && region.end - region.start <= MIN_REGION + 1e-3) {
      clip.regions.splice(state.activeRegion, 1);
      state.activeRegion = -1;
      toast("Drag to size the region.", true);
    } else {
      setMarking(false);
      toast("Region added.");
    }
  }
  if (clip && clip.regions) clip.regions.forEach((r, i) => { if (i === 0) r.join = "cut"; });
  // The gesture is over: land on the exact frame the playhead shows.
  settleSeek();
  drawRegions();
  drawOverview();
  refreshAll();
});

function handleDrag(event) {
  const clip = state.project.clips[state.selected];
  const drag = state.drag;
  if (!clip || !drag) return;
  const duration = currentDuration();
  const at = positionToTime(event.clientX);

  if (drag.kind === "scrub") {
    seekTo(at);
    return;
  }

  const regions = clip.regions;
  const region = regions[drag.index];
  if (!region) return;
  const [low, high] = regionBounds(regions, drag.index, duration);

  if (drag.kind === "resize") {
    if (drag.edge === "start") {
      region.start = Math.max(low, Math.min(at, region.end - MIN_REGION));
      seekTo(region.start);
    } else {
      region.end = Math.min(high, Math.max(at, region.start + MIN_REGION));
      seekTo(region.end);
    }
  } else if (drag.kind === "move") {
    const span = drag.span;
    const start = Math.max(low, Math.min(at - drag.grab, high - span));
    region.start = start;
    region.end = start + span;
    seekTo(start);
  } else if (drag.kind === "create") {
    if (at >= drag.anchor) {
      region.start = Math.max(low, drag.anchor);
      region.end = Math.min(high, Math.max(at, drag.anchor + MIN_REGION));
    } else {
      region.start = Math.max(low, Math.min(at, drag.anchor - MIN_REGION));
      region.end = Math.min(high, drag.anchor);
    }
    seekTo(at);
  }

  drawRegions();
  drawOverview();
}

// -- marking a region from the playhead

export function playheadTime() {
  if (video.src && !video.classList.contains("hidden")) return video.currentTime;
  return parseTime($("time-readout").textContent.split("/")[0]) || 0;
}

$("mark-in").addEventListener("click", () => {
  const clip = state.project.clips[state.selected];
  if (!clip || !currentDuration()) return;
  state.marking = true;
  state.markStart = playheadTime();
  $("scrubber").classList.add("marking");
  $("mark-region").classList.add("primary");
  $("mark-hint").textContent =
    `Region starts at ${fmtPrecise(state.markStart)} — scrub, then press ] or End. Esc to cancel.`;
});

$("mark-out").addEventListener("click", () => {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  if (state.markStart === null) {
    toast("Set the region start first.", true);
    return;
  }
  const a = state.markStart;
  const b = playheadTime();
  const [start, end] = a < b ? [a, b] : [b, a];
  if (end - start < MIN_REGION) {
    toast(`Regions must be at least ${MIN_REGION}s.`, true);
    return;
  }

  clip.regions = clip.regions || [];
  const clash = clip.regions.find((r) => start < r.end - 1e-6 && end > r.start + 1e-6);
  if (clash) {
    toast("That overlaps an existing region.", true);
    return;
  }

  clip.regions.push(newRegion(start, end));
  clip.regions.sort((x, y) => x.start - y.start);
  clip.regions[0].join = "cut";
  state.activeRegion = clip.regions.findIndex((r) => r.start === start);
  setMarking(false);
  drawRegions();
  drawOverview();
  refreshAll();
  toast("Region added.");
});

$("clear-regions").addEventListener("click", () => {
  const clip = state.project.clips[state.selected];
  if (!clip) return;
  clip.regions = [];
  state.activeRegion = -1;
  drawRegions();
  drawOverview();
  refreshAll();
});

document.addEventListener("keydown", (event) => {
  if (!$("page-edit").classList.contains("active")) return;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
  if (event.key === "[") $("mark-in").click();
  else if (event.key === "]") $("mark-out").click();
  else if (event.key === "Escape") setMarking(false);
  else if (event.key === "+" || event.key === "=") zoomBy(2, playheadTime(), 0.5);
  else if (event.key === "-" || event.key === "_") zoomBy(0.5, playheadTime(), 0.5);
  else if (event.key === "0") resetView();
  else if (event.key === "Delete" && state.activeRegion >= 0) {
    const clip = state.project.clips[state.selected];
    clip.regions.splice(state.activeRegion, 1);
    if (clip.regions.length) clip.regions[0].join = "cut";
    state.activeRegion = -1;
    drawRegions();
    drawOverview();
    refreshAll();
  } else if (event.key === "Delete" && state.selected >= 0) {
    // No region highlighted, so Delete means the clip itself; undoable.
    removeClip(state.selected);
  } else if (event.key === " ") {
    event.preventDefault();
    $("play-toggle").click();
  }
});
