/**
 * NudgeOS nudger: every minute, asks the Python brain "should we nudge?"
 * and, if yes, shows a small popup in the corner of the screen.
 *
 * Test mode (shows a nudge right away, ignores cooldowns):
 *   Mac/Linux:  NUDGEOS_FORCE=1 npm start
 *   Windows:    set NUDGEOS_FORCE=1 && npm start
 */
const { BrowserWindow, ipcMain, screen } = require("electron");
const { execFile } = require("child_process");
const path = require("path");

const PYTHON = process.env.NUDGEOS_PYTHON || (process.platform === "win32" ? "python" : "python3");
const BRAIN = path.join(__dirname, "brain", "nudgeos_brain.py");
const CHECK_EVERY_MS = 60 * 1000;
const FORCE = !!process.env.NUDGEOS_FORCE;

let dbPath = null;
let win = null;

function brain(args) {
  return new Promise((resolve) => {
    execFile(PYTHON, [BRAIN, dbPath, ...args.map(String)], (err, stdout, stderr) => {
      if (err) {
        console.error("[nudgeos] brain error:", stderr || err.message);
        return resolve(null);
      }
      try {
        resolve(JSON.parse(stdout.trim().split("\n").pop()));
      } catch (e) {
        console.error("[nudgeos] bad brain output:", stdout);
        resolve(null);
      }
    });
  });
}

function showPopup(nudge) {
  if (win) return; // one popup at a time

  const W = 340, H = 250;
  const area = screen.getPrimaryDisplay().workArea;

  win = new BrowserWindow({
    width: W,
    height: H,
    x: area.x + area.width - W - 16,
    y: area.y + area.height - H - 16,
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    //transparent: true,
    backgroundColor: "#16181d",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadFile(path.join(__dirname, "popup.html"));
  win.webContents.on("console-message", (...args) => {
  const e = args[0];
  console.log("[popup console]", e && e.message !== undefined ? e.message : args[2]);
});
win.webContents.on("preload-error", (_e, p, err) => console.log("[popup preload error]", p, err));
win.webContents.on("did-fail-load", (_e, code, desc) => console.log("[popup load fail]", code, desc));
  win.webContents.once("did-finish-load", () => {
    win.webContents.send("nudge:show", nudge);
    win.showInactive(); // appear WITHOUT stealing keyboard focus from your editor
  });
  win.on("closed", () => { win = null; });
}

async function check() {
  if (win) return;
  const result = await brain(["decide", ...(FORCE ? ["--force"] : [])]);
  console.log("[nudgeos] decide:", JSON.stringify(result));
  if (result && result.nudge) showPopup(result);
}

function start(databasePath) {
  dbPath = databasePath;

  ipcMain.on("nudge:feedback", async (_e, p) => {
    await brain(["feedback", p.nudgeId, p.outcome, p.energyBefore ?? "-", p.energyAfter ?? "-"]);
  });
  ipcMain.on("nudge:close", () => { if (win) win.close(); });

  setTimeout(check, FORCE ? 3000 : CHECK_EVERY_MS);
  setInterval(check, CHECK_EVERY_MS);
}

module.exports = { start };
