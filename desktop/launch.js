// Launches Electron with a clean environment.
//
// VS Code (and any Electron-based terminal host) exports ELECTRON_RUN_AS_NODE=1.
// Inherited, it makes Electron run main.js as plain Node, where require("electron")
// yields a path string instead of the API and startup dies on `app.whenReady`.
// Run under plain Node, require("electron") gives us the executable to re-spawn.

const { spawn } = require("child_process");
const electron = require("electron");

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(electron, ["."], { stdio: "inherit", env, cwd: __dirname + "/.." });
child.on("exit", (code) => process.exit(code === null ? 1 : code));
