"""
NudgeOS core: local-first schema + rule-based fatigue score.

Privacy rule: we store METADATA only (app category, idle flags, counts).
Never keystrokes, never screenshots, never window titles.
"""
import sqlite3
import time
from dataclasses import dataclass

SCHEMA = """
PRAGMA journal_mode = WAL;

-- One row per ~30s sample from the activity monitor
CREATE TABLE IF NOT EXISTS activity_events (
    id            INTEGER PRIMARY KEY,
    ts            INTEGER NOT NULL,          -- unix seconds
    session_id    INTEGER NOT NULL,
    app_category  TEXT NOT NULL,             -- 'code' | 'browser' | 'comms' | 'media' | 'other'
    idle          INTEGER NOT NULL DEFAULT 0,-- 1 if idle during this sample
    input_count   INTEGER NOT NULL DEFAULT 0 -- number of input EVENTS, not what was typed
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON activity_events(ts);

-- A continuous stretch of work, closed by a break or long idle
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY,
    start_ts   INTEGER NOT NULL,
    end_ts     INTEGER
);

-- Voluntary one-tap energy check-ins (1 = drained, 3 = great)
CREATE TABLE IF NOT EXISTS checkins (
    id      INTEGER PRIMARY KEY,
    ts      INTEGER NOT NULL,
    energy  INTEGER NOT NULL CHECK (energy BETWEEN 1 AND 3),
    stress  INTEGER CHECK (stress BETWEEN 1 AND 3)
);

-- The catalog of break types the bandit chooses from
CREATE TABLE IF NOT EXISTS interventions (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,          -- 'walk_2min', 'look_away', 'music', ...
    duration_s INTEGER NOT NULL
);

-- Every nudge we send, plus what happened. 'ignored' and 'snoozed' are signals too.
CREATE TABLE IF NOT EXISTS nudges (
    id               INTEGER PRIMARY KEY,
    ts               INTEGER NOT NULL,
    intervention_id  INTEGER REFERENCES interventions(id),  -- NULL = baseline "no intervention" arm
    fatigue_score    REAL NOT NULL,          -- score at send time (the context)
    outcome          TEXT NOT NULL DEFAULT 'pending',       -- 'taken' | 'snoozed' | 'dismissed' | 'ignored' | 'pending'
    energy_before    INTEGER,
    energy_after     INTEGER                 -- the one-tap reward signal
);
"""

SEED_INTERVENTIONS = [
    ("walk_2min", 120),
    ("look_away_20s", 20),
    ("calming_music", 180),
    ("stretch_90s", 90),
    ("water_and_breathe", 120),
]


def init_db(path: str = "nudgeos.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT OR IGNORE INTO interventions (name, duration_s) VALUES (?, ?)",
        SEED_INTERVENTIONS,
    )
    conn.commit()
    return conn


# ---------- Fatigue score (v0: transparent weighted model) ----------

@dataclass
class Signals:
    continuous_work_min: float      # minutes since last real break
    screen_time_today_min: float    # total active screen minutes today
    min_since_last_break: float
    latest_energy: int | None       # 1-3 from most recent check-in, or None
    input_repetitiveness: float     # 0-1, how repetitive the input pattern is (weak signal!)


# Weights sum to 1.0. Tune these; later, personalize them per user.
WEIGHTS = {
    "continuous": 0.35,
    "screen_day": 0.15,
    "since_break": 0.25,
    "energy": 0.20,
    "repetitive": 0.05,   # deliberately tiny: mouse patterns alone prove nothing
}


def _squash(x: float, full_at: float) -> float:
    """Map 0..full_at minutes to 0..1, capped."""
    return max(0.0, min(1.0, x / full_at))


def fatigue_score(s: Signals) -> float:
    """Returns 0-100. This is an ESTIMATE of recovery need, not a medical measurement."""
    parts = {
        "continuous": _squash(s.continuous_work_min, 90),
        "screen_day": _squash(s.screen_time_today_min, 360),
        "since_break": _squash(s.min_since_last_break, 75),
        # No check-in yet? Stay neutral (0.5) instead of guessing.
        "energy": 0.5 if s.latest_energy is None else (3 - s.latest_energy) / 2,
        "repetitive": max(0.0, min(1.0, s.input_repetitiveness)),
    }
    return round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()), 1)


def explain(s: Signals) -> str:
    """Human-readable reason, so every nudge can say WHY it fired."""
    reasons = []
    if s.continuous_work_min >= 60:
        reasons.append(f"{int(s.continuous_work_min)} min of continuous work")
    if s.min_since_last_break >= 60:
        reasons.append(f"no break in {int(s.min_since_last_break)} min")
    if s.latest_energy == 1:
        reasons.append("you reported low energy")
    return " + ".join(reasons) or "a gentle check-in"


if __name__ == "__main__":
    conn = init_db(":memory:")
    demo = Signals(
        continuous_work_min=90,
        screen_time_today_min=240,
        min_since_last_break=85,
        latest_energy=2,
        input_repetitiveness=0.7,
    )
    print("score:", fatigue_score(demo), "| why:", explain(demo))
