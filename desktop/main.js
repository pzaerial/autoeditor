// Electron shell: spawns the Python backend, then shows it in a native window.
// The UI itself is the same code a browser gets from `python app.py`.

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const net = require("net");
const path = require("path");

const ROOT = path.join(__dirname, "..");

let backend = null;
let port = 0;

/** An OS-assigned free port, so two copies never collide. */
function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

/**
 * How to start the backend. A packaged build ships it frozen next to the app
 * and needs no Python at all; a checkout runs it from source, preferring a
 * local .venv. Looking for the frozen one first is what lets the same shell
 * serve both without a build flag.
 */
function backendCommand() {
  const fs = require("fs");
  const exe = process.platform === "win32" ? "autoeditor-backend.exe" : "autoeditor-backend";
  for (const dir of [process.resourcesPath || "", ROOT]) {
    const frozen = path.join(dir, "backend", exe);
    if (dir && fs.existsSync(frozen)) return { command: frozen, args: [] };
  }
  const venv = process.platform === "win32"
    ? path.join(ROOT, ".venv", "Scripts", "python.exe")
    : path.join(ROOT, ".venv", "bin", "python");
  const python = fs.existsSync(venv)
    ? venv
    : (process.platform === "win32" ? "python" : "python3");
  return { command: python, args: ["app.py"] };
}

function startBackend() {
  const { command, args } = backendCommand();
  backend = spawn(command, [...args, "--port", String(port), "--no-browser"], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });

  backend.stdout.on("data", (d) => process.stdout.write(`[py] ${d}`));
  backend.stderr.on("data", (d) => process.stderr.write(`[py] ${d}`));

  backend.on("error", (err) => {
    dialog.showErrorBox(
      "Could not start the backend",
      `Failed to run "${command}".\n\n${err.message}\n\n` +
      "Python 3.10+ and ffmpeg must be installed and on your PATH."
    );
    app.quit();
  });

  backend.on("exit", (code) => {
    if (code !== 0 && code !== null && !app.isQuitting) {
      dialog.showErrorBox(
        "Backend stopped",
        `The Python backend exited with code ${code}. ` +
        "Check that ffmpeg and ffprobe are on your PATH."
      );
      app.quit();
    }
  });
}

/** Poll until the server answers, so the window never shows a connection error. */
function waitForBackend(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const socket = net.connect(port, "127.0.0.1");
      socket.on("connect", () => {
        socket.end();
        resolve();
      });
      socket.on("error", () => {
        socket.destroy();
        if (Date.now() > deadline) reject(new Error("backend did not start in time"));
        else setTimeout(attempt, 200);
      });
    };
    attempt();
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 1000,
    minHeight: 700,
    backgroundColor: "#14161a",
    title: "Auto Editor",
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  win.removeMenu();
  win.loadURL(`http://127.0.0.1:${port}/`);
  win.once("ready-to-show", () => win.show());

  // Anything aimed at a new window opens in the real browser instead.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
}

// Revealing a file has to happen here rather than in the backend: Windows only
// lets the foreground process pass focus along, and that is this process.
ipcMain.handle("desktop:reveal", (_event, target) => {
  const fs = require("fs");
  if (!target || !fs.existsSync(target)) return false;
  shell.showItemInFolder(path.normalize(target));
  return true;
});

app.whenReady().then(async () => {
  try {
    port = await freePort();
    startBackend();
    await waitForBackend();
    createWindow();
  } catch (err) {
    dialog.showErrorBox("Startup failed", String(err));
    app.quit();
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", () => {
  app.isQuitting = true;
});

app.on("window-all-closed", () => app.quit());

// Never leave an orphaned Python process behind.
app.on("quit", () => backend && backend.kill());
process.on("exit", () => backend && backend.kill());
