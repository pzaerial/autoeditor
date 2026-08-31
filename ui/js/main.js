// Entry point. Imports every module for its side effects (each wires up its own
// listeners as it loads), then starts the app.

import * as balance from "./balance.js";
import * as clips from "./clips.js";
import * as inspector from "./inspector.js";
import * as library from "./library.js";
import * as pages from "./pages.js";
import * as picker from "./picker.js";
import * as preview from "./preview.js";
import * as render from "./render.js";
import * as settings from "./settings.js";
import * as state from "./state.js";
import * as timeline from "./timeline.js";
import * as tracklanes from "./tracklanes.js";
import * as util from "./util.js";
import * as views from "./views.js";

// A deliberate seam. The app runs in a window with no devtools and no address
// bar, so there is otherwise no way to look at it while it is running, and no
// way for a test to drive it. Modules keep their own scope; this is the one
// place that opens them, and it exports nothing that is not already exported
// somewhere. `modules` holds the namespace objects, whose bindings stay live --
// the flattened copy is a snapshot, which is fine for functions and wrong for
// anything that changes.
const modules = {
  balance, clips, inspector, library, pages, picker, preview, render,
  settings, state, timeline, tracklanes, util, views,
};
window.app = Object.assign({}, ...Object.values(modules), { modules });

// The timeline, the inspector and the lane list all edit the same project, so
// they all refresh through one place rather than each other.
function changed() {
  views.refreshAll();
}
timeline.bindTimeline({
  change: changed,
  seek: (at) => preview.showAt(at),
  select: () => {
    inspector.drawInspector();
    const entry = state.selectedEntry();
    if (entry && entry.type !== "transition") {
      const placed = state.layout(state.trackAt(state.state.selected.track))
        .find((p) => p.clip === entry);
      if (placed) preview.showAt(placed.start);
    }
  },
});
inspector.bindInspector(changed);
tracklanes.bindLanes(changed);

timeline.initTimeline();

util.$("tl-zoom-in").addEventListener("click", () => {
  timeline.zoomBy(1 / 1.5);
  timeline.drawTimeline();
});
util.$("tl-zoom-out").addEventListener("click", () => {
  timeline.zoomBy(1.5);
  timeline.drawTimeline();
});
util.$("tl-zoom-fit").addEventListener("click", () => {
  timeline.resetView();
  timeline.drawTimeline();
});
util.$("tl-add-video").addEventListener("click", () => tracklanes.addTrack("video"));
util.$("tl-add-audio").addEventListener("click", () => tracklanes.addTrack("audio"));
util.$("tl-add-clips").addEventListener("click", () => picker.openPicker(-1));

// The library decides what the inspector can offer and how the timeline lays
// clips out, so it is fetched before anything is drawn.
await library.loadLibrary();

settings.projectToForm();
balance.showAutoEditorState();
settings.loadEncoders();
settings.loadTemplates();
balance.adoptRunningBalance();
picker.updatePickerCount();
views.refreshAll();
// So the transport reads the real length from the start, rather than 0:00 of
// 0:00 until something is clicked.
preview.showAt(0);
pages.showPage("settings");
render.pollRender();
