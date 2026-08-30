// The only bridge between the page and the desktop shell.
//
// Everything else the UI needs it gets over HTTP from the Python backend, which
// works identically in a browser. This exists for the one thing a local HTTP
// server cannot do: reveal a file in the file manager *and take the foreground*.
// Windows only lets the foreground process hand focus on, and that is Electron,
// not the backend it spawned -- which is why a folder opened from Python lands
// behind the app window.
//
// `window.desktop` is absent in a browser, and the UI falls back to the backend.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktop", {
  showItemInFolder: (path) => ipcRenderer.invoke("desktop:reveal", path),
});
