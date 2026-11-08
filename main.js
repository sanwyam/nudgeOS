/**
 * NudgeOS tracker (Electron main process)
 *
 * Every 30 seconds it saves ONE small row:
 *   - what KIND of app is in front (code / browser / comms / media / other)
 *   - whether you were idle
 *   - which work session this belongs to
 *
 * It NEVER saves: keystrokes, screenshots, window titles, or file names.
 */
const { app, powerMonitor } = require("electron");
const path = require("path");
const Database = require("better-sqlite3");
app.disableHardwareAcceleration();

const SAMPLE_EVERY_MS = 30 * 1000;
const IDLE_AFTER_S = 60;          // no input for 1 min = idle
const BREAK_AFTER_IDLE_S = 5 * 60; // idle for 5 min = you took a real break, session ends

// ---- App name -> category. Add your own apps here! ----
const CATEGORIES = {
  code: ["code", "visual studio", "intellij", "pycharm", "webstorm", "terminal", "iterm", "powershell", "cmd", "android studio"],
  browser: ["chrome", "firefox", "safari", "edge", "brave", "arc"],
  comms: ["slack", "discord", "teams", "zoom", "whatsapp", "telegram", "outlook", "mail"],
  media: ["spotify", "vlc", "youtube", "netflix", "music"],
};

function categorize(appName = "") {
  const name = appName.toLowerCase();
  for (const [category, keywords] of Object.entries(CATEGORIES)) {
    if (keywords.some((k) => name.includes(k))) return category;
  }
  return "other";
}

// ---- Database (same tables as nudgeos_core.py) ----
function openDb() {
  const db = new Database(path.join(app.getPath("userData"), "nudgeos.db"));
  db.pragma("journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      id INTEGER PRIMARY KEY, start_ts INTEGER NOT NULL, end_ts INTEGER
    );
    CREATE TABLE IF NOT EXISTS activity_events (
      id INTEGER PRIMARY KEY,
      ts INTEGER NOT NULL,
      session_id INTEGER NOT NULL,
      app_category TEXT NOT NULL,
      idle INTEGER NOT NULL DEFAULT 0,
      input_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_events_ts ON activity_events(ts);
  `);
  return db;
}

let db;
let currentSessionId = null;

function startSession(now) {
  const info = db.prepare("INSERT INTO sessions (start_ts) VALUES (?)").run(now);
  currentSessionId = info.lastInsertRowid;
}

function endSession(now) {
  if (currentSessionId !== null) {
    db.prepare("UPDATE sessions SET end_ts = ? WHERE id = ?").run(now, currentSessionId);
    currentSessionId = null;
  }
}

async function sample() {
  const now = Math.floor(Date.now() / 1000);
  const idleSeconds = powerMonitor.getSystemIdleTime(); // seconds since last input

  // Long idle = a real break. Close the session and wait for the user to come back.
  if (idleSeconds >= BREAK_AFTER_IDLE_S) {
    endSession(now - idleSeconds);
    return;
  }
  if (currentSessionId === null) startSession(now);

  // active-win v8 is CommonJS. We keep ONLY the app name, then throw it away after categorizing.
  let category = "other";
  try {
    const win = await require("active-win")();
    category = categorize(win && win.owner && win.owner.name);
  } catch (_) {
    // Some OSes need extra permission; fall back to "other" instead of crashing.
  }

  db.prepare(
    "INSERT INTO activity_events (ts, session_id, app_category, idle, input_count) VALUES (?, ?, ?, ?, 0)"
  ).run(now, currentSessionId, category, idleSeconds >= IDLE_AFTER_S ? 1 : 0);

  console.log(`[nudgeos] ${category.padEnd(8)} idle=${idleSeconds >= IDLE_AFTER_S ? "yes" : "no "} session=${currentSessionId}`);
}

app.whenReady().then(() => {
  db = openDb();
  sample();
  setInterval(sample, SAMPLE_EVERY_MS);
  require("./nudger").start(path.join(app.getPath("userData"), "nudgeos.db"));
  // No window yet: this is a background-only tracker for now.
});

// Keep running with no windows open (Windows/Linux would otherwise quit).
app.on("window-all-closed", (e) => e.preventDefault());

app.on("before-quit", () => {
  if (db) endSession(Math.floor(Date.now() / 1000));
});
