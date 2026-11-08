"""
NudgeOS report: builds a "did it help?" dashboard from your local database.

  python3 brain/nudgeos_report.py            (finds your database automatically)
  python3 brain/nudgeos_report.py my.db      (or point at a specific one)
  python3 brain/nudgeos_report.py --no-open  (write the file, don't open the browser)

Writes dashboard.html next to the database (NOT inside your git repo) and opens it.
Everything is read-only: this script never changes your data.
"""
import html
import os
import sqlite3
import sys
import time
import webbrowser
from datetime import datetime, timedelta

LABELS = {
    "walk_2min": ("🚶", "2-min walk"),
    "look_away_20s": ("👀", "Look away 20s"),
    "calming_music": ("🎧", "Calm song"),
    "stretch_90s": ("🙆", "90s stretch"),
    "water_and_breathe": ("💧", "Water + breathe"),
}
MIN_EVIDENCE = 3  # need at least this many usable results before calling anything a favorite


def default_db_path() -> str:
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA", home)
    else:
        base = os.path.join(home, ".config")
    return os.path.join(base, "nudgeos-tracker", "nudgeos.db")


def e(x) -> str:
    return html.escape(str(x))


def collect(conn):
    rows = conn.execute(
        """SELECT n.id, n.ts, i.name, n.fatigue_score, n.outcome, n.energy_before, n.energy_after
           FROM nudges n LEFT JOIN interventions i ON i.id = n.intervention_id
           ORDER BY n.ts DESC"""
    ).fetchall()

    stats = {}
    for _id, ts, name, score, outcome, before, after in rows:
        if outcome == "pending" or name is None:
            continue
        s = stats.setdefault(name, dict(shown=0, taken=0, snoozed=0, skipped=0,
                                        lifts=[], ceiling=0))
        s["shown"] += 1
        if outcome == "taken":
            s["taken"] += 1
            if before is not None and after is not None:
                if before >= 3:
                    s["ceiling"] += 1          # already at max energy: can't show improvement
                else:
                    s["lifts"].append(after - before)
        elif outcome == "snoozed":
            s["snoozed"] += 1
        else:                                  # dismissed / ignored
            s["skipped"] += 1

    # Active minutes per day, last 7 days
    today = datetime.now().date()
    days = [today - timedelta(days=k) for k in range(6, -1, -1)]
    active = {d: 0.0 for d in days}
    for day_str, n in conn.execute(
        """SELECT date(ts, 'unixepoch', 'localtime'), COUNT(*)
           FROM activity_events WHERE idle = 0 GROUP BY 1"""
    ):
        d = datetime.strptime(day_str, "%Y-%m-%d").date()
        if d in active:
            active[d] = n * 30 / 60
    return rows, stats, active


def build_html(rows, stats, active) -> str:
    total_shown = sum(s["shown"] for s in stats.values())
    total_taken = sum(s["taken"] for s in stats.values())
    all_lifts = [x for s in stats.values() for x in s["lifts"]]
    avg_lift = sum(all_lifts) / len(all_lifts) if all_lifts else None

    # ---- per-break cards ----
    ranked = []
    for name, s in stats.items():
        n = len(s["lifts"])
        helped = sum(1 for x in s["lifts"] if x > 0)
        rate = helped / n if n else None
        ranked.append((name, s, n, helped, rate))
    ranked.sort(key=lambda r: (-(r[4] if r[4] is not None else -1), -r[2]))

    leader = next((r for r in ranked if r[2] >= MIN_EVIDENCE and r[4] is not None), None)

    cards = []
    for name, s, n, helped, rate in ranked:
        emoji, label = LABELS.get(name, ("✨", name))
        bar = int((rate or 0) * 100)
        rate_txt = f"{bar}% helped" if rate is not None else "no usable results yet"
        crown = " 👑" if leader and leader[0] == name else ""
        learning = "" if n >= MIN_EVIDENCE else '<span class="tag">still learning</span>'
        lift_txt = f"{sum(s['lifts'])/n:+.1f}" if n else "–"
        ceil = f" · {s['ceiling']} at max energy (not counted)" if s["ceiling"] else ""
        cards.append(f"""
        <div class="card">
          <div class="row"><span class="big">{emoji}</span>
            <div><b>{e(label)}{crown}</b> {learning}<br>
            <span class="muted">shown {s['shown']}× · done {s['taken']} · snoozed {s['snoozed']} · skipped {s['skipped']}{e(ceil)}</span></div>
          </div>
          <div class="track"><div class="fill" style="width:{bar}%"></div></div>
          <div class="row spread"><span>{e(rate_txt)} <span class="muted">(out of {n})</span></span>
            <span>avg energy change <b>{lift_txt}</b></span></div>
        </div>""")
    cards_html = "\n".join(cards) or '<p class="muted">No nudges answered yet. Use NudgeOS for a day and come back!</p>'

    # ---- active time chart ----
    peak = max(active.values()) or 1
    bars = []
    for d, mins in active.items():
        h = int(100 * mins / peak)
        bars.append(f'<div class="col"><div class="barwrap"><div class="bar" style="height:{h}%"></div></div>'
                    f'<div class="lbl">{d.strftime("%a")}</div><div class="muted">{mins/60:.1f}h</div></div>')

    # ---- recent nudges ----
    recent = []
    for _id, ts, name, score, outcome, before, after in rows[:10]:
        emoji, label = LABELS.get(name, ("✨", name or "baseline"))
        when = datetime.fromtimestamp(ts).strftime("%a %H:%M")
        change = f"{before} → {after}" if before is not None and after is not None else "–"
        recent.append(f"<tr><td>{when}</td><td>{emoji} {e(label)}</td><td>{e(outcome)}</td>"
                      f"<td>{change}</td><td>{score:.0f}</td></tr>")
    recent_html = "\n".join(recent) or '<tr><td colspan="5" class="muted">Nothing yet.</td></tr>'

    headline = (f"{avg_lift:+.2f}" if avg_lift is not None else "–")
    verdict = (f"{leader and LABELS.get(leader[0], ('', leader[0]))[1]} is working best for you so far."
               if leader else "Still collecting data. Rankings appear after a few results per break.")

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>NudgeOS: did it help?</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --text:#16181d; --muted:#6b7280; --line:#e5e7eb; --accent:#4f6bff; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#16181d; --text:#f2f3f5; --muted:#9aa0ac; --line:#2a2e37; --accent:#6c8cff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:860px; margin:0 auto; padding:28px 18px 60px; }}
h1 {{ margin:0 0 4px; font-size:26px; }} h2 {{ margin:34px 0 12px; font-size:18px; }}
.muted {{ color:var(--muted); font-size:13px; }}
.stats {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-top:18px; }}
.stat, .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; }}
.stat b {{ display:block; font-size:28px; margin-top:4px; }}
.card {{ margin-bottom:12px; }} .row {{ display:flex; gap:12px; align-items:center; }} .spread {{ justify-content:space-between; margin-top:8px; font-size:14px; }}
.big {{ font-size:30px; }}
.track {{ height:10px; background:var(--line); border-radius:99px; margin-top:12px; overflow:hidden; }}
.fill {{ height:100%; background:var(--accent); border-radius:99px; }}
.tag {{ background:var(--line); color:var(--muted); font-size:11px; padding:2px 8px; border-radius:99px; margin-left:6px; }}
.chart {{ display:flex; gap:10px; align-items:flex-end; height:170px; }}
.col {{ flex:1; text-align:center; }} .barwrap {{ height:110px; display:flex; align-items:flex-end; }}
.bar {{ width:100%; background:var(--accent); border-radius:8px 8px 0 0; min-height:2px; }}
.lbl {{ margin-top:6px; font-size:13px; }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; font-size:14px; }}
td, th {{ padding:10px 12px; text-align:left; border-bottom:1px solid var(--line); }} th {{ color:var(--muted); font-weight:600; font-size:12px; }}
.note {{ margin-top:30px; padding:14px 16px; border:1px dashed var(--line); border-radius:12px; font-size:13px; color:var(--muted); line-height:1.5; }}
@media (max-width:560px) {{ .stats {{ grid-template-columns:1fr; }} }}
</style></head><body><main>
<h1>Did it help? 📈</h1>
<div class="muted">{e(verdict)}</div>
<div class="stats">
  <div class="stat"><span class="muted">Nudges answered</span><b>{total_shown}</b></div>
  <div class="stat"><span class="muted">Breaks you took</span><b>{total_taken}</b></div>
  <div class="stat"><span class="muted">Avg energy change (1–3 scale)</span><b>{headline}</b></div>
</div>
<h2>Which breaks work for you</h2>
{cards_html}
<h2>Active screen time, last 7 days</h2>
<div class="card"><div class="chart">{"".join(bars)}</div></div>
<h2>Recent nudges</h2>
<table><tr><th>When</th><th>Break</th><th>What you did</th><th>Energy</th><th>Tiredness score</th></tr>
{recent_html}</table>
<div class="note"><b>Read this with a pinch of salt.</b> These are your own 1–3 taps, not medical measurements.
Energy often bounces back after <i>any</i> pause, so a break "helping" is a correlation, not proof it caused the change.
Results where you were already at max energy are left out, since they can't show improvement.
More data makes this more meaningful, so check back after a couple of weeks.</div>
</main></body></html>"""


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    db_path = args[0] if args else default_db_path()
    if not os.path.exists(db_path):
        sys.exit(f"Can't find a database at {db_path}\nRun the app once first, or pass the path as an argument.")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows, stats, active = collect(conn)
    out = os.path.join(os.path.dirname(os.path.abspath(db_path)), "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_html(rows, stats, active))
    print(f"Dashboard written to {out}")
    if "--no-open" not in sys.argv:
        webbrowser.open("file://" + out)
