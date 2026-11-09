"""
NudgeOS bandit: picks WHICH break to suggest, and learns what works.

Plain-English version:
  - Every break type has a scoreboard: "times it helped" vs "times it didn't".
  - To pick a break, we ask each one for a lucky guess of how good it is
    (the guess is wide when we know little, narrow when we know a lot).
  - Highest guess wins. New breaks get tried, proven breaks get repeated.
  - That trick is called Thompson sampling.

Needs nudgeos_core.py in the same folder.
"""
import random
import time

from nudgeos_core import init_db

BASELINE = None  # the "do nothing" option, so we can check breaks beat doing nothing


def fatigue_bucket(score: float) -> str:
    """Different breaks may work at different tiredness levels, so we track them separately."""
    if score < 40:
        return "low"
    if score < 70:
        return "medium"
    return "high"


# ---------- Reading the scoreboard ----------

def load_scoreboard(conn, bucket: str) -> dict:
    """
    Returns {intervention_id_or_None: (wins, losses)} for this tiredness bucket.
      win  = user took the break and energy went UP
      loss = user took it and energy did NOT go up, or dismissed the nudge
    Snoozed / ignored / pending are skipped: they don't tell us the break was bad.
    """
    low, high = {"low": (0, 40), "medium": (40, 70), "high": (70, 101)}[bucket]
    rows = conn.execute(
        """
        SELECT intervention_id, outcome, energy_before, energy_after
        FROM nudges
        WHERE fatigue_score >= ? AND fatigue_score < ?
        """,
        (low, high),
    ).fetchall()

    board = {}
    for iid, outcome, before, after in rows:
        wins, losses = board.get(iid, (0, 0))
        if outcome == "taken" and before is not None and after is not None:
            if before >= 3:
                continue  # already at max energy: can't improve, so it says nothing about the break
            if after > before:
                wins += 1
            else:
                losses += 1
        elif outcome == "dismissed":
            losses += 1
        else:
            continue
        board[iid] = (wins, losses)
    return board


# ---------- Picking a break ----------

def choose_intervention(conn, fatigue_score: float, include_baseline_pct: float = 0.1):
    """
    Returns an intervention id (or None for the baseline "no nudge" arm).
    include_baseline_pct: how often we deliberately do nothing, to measure honestly.
    """
    ids = [r[0] for r in conn.execute("SELECT id FROM interventions")]
    board = load_scoreboard(conn, fatigue_bucket(fatigue_score))

    # Small chance to run a baseline, so we learn if breaks beat plain time passing.
    if random.random() < include_baseline_pct:
        return BASELINE

    best_id, best_draw = None, -1.0
    for iid in ids:
        wins, losses = board.get(iid, (0, 0))
        # Beta(1+wins, 1+losses): starts as a coin flip, sharpens with data.
        draw = random.betavariate(1 + wins, 1 + losses)
        if draw > best_draw:
            best_id, best_draw = iid, draw
    return best_id


# ---------- Logging what happened ----------

def record_nudge(conn, intervention_id, fatigue_score, energy_before=None) -> int:
    cur = conn.execute(
        "INSERT INTO nudges (ts, intervention_id, fatigue_score, energy_before) VALUES (?, ?, ?, ?)",
        (int(time.time()), intervention_id, fatigue_score, energy_before),
    )
    conn.commit()
    return cur.lastrowid


def record_outcome(conn, nudge_id: int, outcome: str, energy_after=None):
    conn.execute(
        "UPDATE nudges SET outcome = ?, energy_after = ? WHERE id = ?",
        (outcome, energy_after, nudge_id),
    )
    conn.commit()


# ---------- Demo: a fake user who loves walks ----------

if __name__ == "__main__":
    random.seed(7)
    conn = init_db(":memory:")
    names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM interventions")}

    # Secret truth the bandit does NOT know: chance each break actually helps this person.
    secret = {"walk_2min": 0.85, "look_away_20s": 0.5, "calming_music": 0.6,
              "stretch_90s": 0.4, "water_and_breathe": 0.3}
    baseline_helps = 0.2

    for day in range(1, 121):
        score = 78.0  # pretend they're always tired when we nudge
        iid = choose_intervention(conn, score)
        nid = record_nudge(conn, iid, score, energy_before=1)
        chance = baseline_helps if iid is None else secret[names[iid]]
        helped = random.random() < chance
        record_outcome(conn, nid, "taken", energy_after=2 if helped else 1)

    print("After 120 nudges, how often did it suggest each break?\n")
    counts = {}
    for (iid,) in conn.execute("SELECT intervention_id FROM nudges"):
        counts[iid] = counts.get(iid, 0) + 1
    for iid, n in sorted(counts.items(), key=lambda x: -x[1]):
        label = "no nudge (baseline)" if iid is None else names[iid]
        print(f"  {label:<22} {n:>3} times  {'#' * (n // 2)}")
