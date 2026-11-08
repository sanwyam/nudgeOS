"""
NudgeOS brain: the command-line bridge Electron talks to.

  python nudgeos_brain.py <db> decide [--force]
  python nudgeos_brain.py <db> feedback <nudge_id> <outcome> <energy_before|-> <energy_after|->
  python nudgeos_brain.py <db> checkin <energy 1-3>

Every command prints ONE line of JSON.

Politeness rules (so the app doesn't become the thing it's trying to fix):
  - only nudge when the fatigue score is high enough
  - max 6 nudges a day
  - wait after each nudge: short after a snooze, long after a dismiss
  - two ignored/dismissed nudges in a row = back off for twice as long
"""
import json
import sys
import time

from nudgeos_core import init_db, fatigue_score, explain
from nudgeos_signals import build_signals, _today_start
from nudgeos_bandit import choose_intervention, record_nudge

THRESHOLD = 60
MAX_PER_DAY = 6
PENDING_EXPIRES_S = 10 * 60
COOLDOWN_MIN = {"taken": 45, "snoozed": 15, "dismissed": 90, "ignored": 90}


def sweep(conn, now):
    """A nudge nobody answered for 10 minutes counts as 'ignored' (that's data too)."""
    conn.execute(
        "UPDATE nudges SET outcome = 'ignored' WHERE outcome = 'pending' AND ts < ?",
        (now - PENDING_EXPIRES_S,),
    )
    conn.commit()


def quiet_reason(conn, now):
    """Returns a reason to stay quiet, or None if we're allowed to nudge."""
    (today_count,) = conn.execute(
        "SELECT COUNT(*) FROM nudges WHERE ts >= ?", (_today_start(now),)
    ).fetchone()
    if today_count >= MAX_PER_DAY:
        return "daily limit reached"

    last = conn.execute("SELECT ts, outcome FROM nudges ORDER BY ts DESC LIMIT 1").fetchone()
    if last:
        ts, outcome = last
        if outcome == "pending":
            return "still waiting on the last nudge"
        wait = COOLDOWN_MIN.get(outcome, 45) * 60
        recent = [r[0] for r in conn.execute("SELECT outcome FROM nudges ORDER BY ts DESC LIMIT 2")]
        if len(recent) == 2 and all(o in ("dismissed", "ignored") for o in recent):
            wait *= 2  # back off when the user keeps waving us away
        if now - ts < wait:
            return "cooling down"
    return None


def decide(conn, now, force=False):
    sweep(conn, now)
    s = build_signals(conn, now)
    score = fatigue_score(s)

    if not force:
        if score < THRESHOLD:
            return {"nudge": False, "score": score, "reason": "not tired enough yet"}
        reason = quiet_reason(conn, now)
        if reason:
            return {"nudge": False, "score": score, "reason": reason}

    # Baseline ("do nothing") arm is OFF for now: it would need its own popup flow.
    iid = choose_intervention(conn, score, include_baseline_pct=0.0)
    nudge_id = record_nudge(conn, iid, score, energy_before=s.latest_energy)
    name, duration = conn.execute(
        "SELECT name, duration_s FROM interventions WHERE id = ?", (iid,)
    ).fetchone()
    return {
        "nudge": True,
        "nudge_id": nudge_id,
        "intervention": name,
        "duration_s": duration,
        "score": score,
        "why": explain(s),
        "energy_before": s.latest_energy,  # None means the popup should ask
    }


def feedback(conn, nudge_id, outcome, energy_before, energy_after):
    conn.execute(
        """UPDATE nudges
           SET outcome = ?, energy_before = COALESCE(?, energy_before), energy_after = ?
           WHERE id = ?""",
        (outcome, energy_before, energy_after, nudge_id),
    )
    if energy_after is not None:  # the "after" tap is also a fresh energy check-in
        conn.execute(
            "INSERT INTO checkins (ts, energy) VALUES (?, ?)", (int(time.time()), energy_after)
        )
    conn.commit()
    return {"ok": True}


def _num(x):
    return None if x in ("-", "", None) else int(x)


if __name__ == "__main__":
    db_path, command, *args = sys.argv[1:]
    conn = init_db(db_path)
    now = int(time.time())

    if command == "decide":
        out = decide(conn, now, force="--force" in args)
    elif command == "feedback":
        nid, outcome, before, after = args
        out = feedback(conn, int(nid), outcome, _num(before), _num(after))
    elif command == "checkin":
        conn.execute("INSERT INTO checkins (ts, energy) VALUES (?, ?)", (now, int(args[0])))
        conn.commit()
        out = {"ok": True}
    else:
        out = {"error": f"unknown command {command}"}

    print(json.dumps(out))
