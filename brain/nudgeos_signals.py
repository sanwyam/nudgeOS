"""
NudgeOS signals: reads what the tracker saved and builds the numbers
the fatigue score needs.

Run on real data:   python nudgeos_signals.py path/to/nudgeos.db
Run the fake demo:  python nudgeos_signals.py

Needs nudgeos_core.py in the same folder.
"""
import sys
import time
from datetime import datetime

from nudgeos_core import Signals, fatigue_score, explain, init_db

SAMPLE_S = 30                 # the tracker saves one row every 30 seconds
MICRO_BREAK_SAMPLES = 4       # 4 idle samples in a row (~2 min) counts as a mini break
CHECKIN_FRESH_S = 3 * 3600    # a check-in older than 3 hours is stale, so ignore it


def _today_start(now: int) -> int:
    d = datetime.fromtimestamp(now)
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def build_signals(conn, now: int | None = None) -> Signals:
    now = now or int(time.time())

    # --- Current session (the one that hasn't been closed by a long idle) ---
    row = conn.execute(
        "SELECT id, start_ts FROM sessions WHERE end_ts IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()

    continuous_work_min = 0.0
    min_since_break = 0.0

    if row:
        session_id, start_ts = row
        events = conn.execute(
            "SELECT ts, idle FROM activity_events WHERE session_id = ? ORDER BY ts",
            (session_id,),
        ).fetchall()

        # Active time = samples where you were NOT idle.
        active_samples = sum(1 for _, idle in events if not idle)
        continuous_work_min = active_samples * SAMPLE_S / 60

        # Time since last break = since the end of the latest idle stretch
        # of ~2+ min, or since the session started if there wasn't one.
        last_break_end_ts = start_ts
        run = 0
        for ts, idle in events:
            if idle:
                run += 1
            else:
                if run >= MICRO_BREAK_SAMPLES:
                    last_break_end_ts = ts
                run = 0
        min_since_break = (now - last_break_end_ts) / 60

    # --- Total screen time today (all sessions, active samples only) ---
    (active_today,) = conn.execute(
        "SELECT COUNT(*) FROM activity_events WHERE ts >= ? AND idle = 0",
        (_today_start(now),),
    ).fetchone()
    screen_time_today_min = active_today * SAMPLE_S / 60

    # --- Latest voluntary check-in (if it's recent enough to mean anything) ---
    latest_energy = None
    c = conn.execute("SELECT ts, energy FROM checkins ORDER BY ts DESC LIMIT 1").fetchone()
    if c and now - c[0] <= CHECKIN_FRESH_S:
        latest_energy = c[1]

    return Signals(
        continuous_work_min=continuous_work_min,
        screen_time_today_min=screen_time_today_min,
        min_since_last_break=min_since_break,
        latest_energy=latest_energy,
        input_repetitiveness=0.0,  # we don't collect input patterns yet (on purpose)
    )


def current_status(conn, now: int | None = None) -> dict:
    s = build_signals(conn, now)
    return {"signals": s, "score": fatigue_score(s), "why": explain(s)}


# ---------- Demo with fake data ----------

def _fake_data(conn, now: int):
    """90 minutes of coding, with one 3-minute pause in the middle."""
    start = now - 90 * 60
    conn.execute("INSERT INTO sessions (id, start_ts) VALUES (1, ?)", (start,))
    for i in range(90 * 2):                       # 180 samples
        ts = start + i * SAMPLE_S
        minute = i / 2
        idle = 1 if 40 <= minute < 43 else 0      # a 3 min pause around minute 40
        conn.execute(
            "INSERT INTO activity_events (ts, session_id, app_category, idle) VALUES (?, 1, 'code', ?)",
            (ts, idle),
        )
    conn.execute("INSERT INTO checkins (ts, energy) VALUES (?, 1)", (now - 10 * 60,))
    conn.commit()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        conn = init_db(sys.argv[1])
        now = int(time.time())
    else:
        conn = init_db(":memory:")
        now = int(time.time())
        _fake_data(conn, now)
        print("(using fake data: 90 min of coding, one 3-min pause, low energy check-in)\n")

    result = current_status(conn, now)
    s = result["signals"]
    print(f"Continuous work : {s.continuous_work_min:.0f} min of active time")
    print(f"Since last break: {s.min_since_last_break:.0f} min")
    print(f"Screen time today: {s.screen_time_today_min:.0f} min")
    print(f"Latest energy   : {s.latest_energy}")
    print(f"\nFatigue score   : {result['score']} / 100")
    print(f"Why             : {result['why']}")
