// Entry point. Imports every module for its side effects (each wires up its own
// listeners as it loads), then starts the app.

import * as balance from "./balance.js";
import * as clips from "./clips.js";
import * as editor from "./editor.js";
import * as pages from "./pages.js";
import * as picker from "./picker.js";
import * as preview from "./preview.js";
import * as rail from "./rail.js";
import * as regions from "./regions.js";
import * as render from "./render.js";
import * as settings from "./settings.js";
import * as state from "./state.js";
import * as util from "./util.js";
import * as views from "./views.js";
import * as zoom from "./zoom.js";

// A deliberate seam. The app runs in a window with no devtools and no address
// bar, so there is otherwise no way to look at it while it is running, and no
// way for a test to drive it. Modules keep their own scope; this is the one
// place that opens them, and it exports nothing that is not already exported
// somewhere. `modules` holds the namespace objects, whose bindings stay live --
// the flattened copy is a snapshot, which is fine for functions and wrong for
// anything that changes.
const modules = {
  balance, clips, editor, pages, picker, preview, rail,
  regions, render, settings, state, util, views, zoom,
};
window.app = Object.assign({}, ...Object.values(modules), { modules });

settings.projectToForm();
balance.showAutoEditorState();
settings.loadEncoders();
settings.loadTemplates();
balance.adoptRunningBalance();
picker.updatePickerCount();
views.refreshAll();
pages.showPage("settings");
render.pollRender();
