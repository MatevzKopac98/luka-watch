"""Print a quick summary of the latest snapshot. Run: python scripts/show_data.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import connect  # noqa: E402

con = connect()
d = con.execute("SELECT MAX(snapshot_date) FROM team_season").fetchone()[0]
print("Snapshot", d)

print("\nPLAYERS")
for r in con.execute("""SELECT p.name, p.team_abbr, s.gp, s.pts, s.reb, s.ast, s.ts_pct, s.td3
                        FROM player_season s JOIN players p USING (espn_id)
                        WHERE s.snapshot_date = ? ORDER BY s.pts DESC""", (d,)):
    print(f"  {r['name']:<26}{r['team_abbr']:<5}GP {r['gp']:<3}{r['pts']:>5} pts {r['reb']:>5} reb"
          f" {r['ast']:>5} ast  TS {r['ts_pct']}%  TD {r['td3']}")

print("\nMVP ODDS (top 6 per source)")
for src in ("Polymarket",):
    rows = con.execute("SELECT player_name, prob FROM mvp_odds WHERE snapshot_date=? AND source=?"
                       " ORDER BY prob DESC LIMIT 6", (d, src)).fetchall()
    print(f"  {src}: " + ", ".join(f"{r['player_name']} {r['prob']:.0%}" for r in rows))

print("\nTEAMS by net rating (top 8 + Lakers)")
rows = con.execute("SELECT * FROM team_season WHERE snapshot_date=? ORDER BY net_rtg DESC", (d,)).fetchall()
for i, r in enumerate(rows, 1):
    if i <= 8 or r["abbr"] == "LAL":
        print(f"  {i:>2}. {r['abbr']:<5}{r['wins']}-{r['losses']:<4}ORtg {r['ortg']:<6}DRtg {r['drtg']:<6}"
              f"Net {r['net_rtg']:<6}Pace {r['pace']}  streak {r['streak']}  L10 {r['last10']}")

g = con.execute("SELECT * FROM games WHERE status != 'STATUS_FINAL' ORDER BY date_utc LIMIT 1").fetchone()
print(f"\nNEXT GAME: {g['away_abbr']} @ {g['home_abbr']}  {g['date_utc']} UTC")
print("Last 3 Luka games:")
for r in con.execute("""SELECT g.* FROM player_games g JOIN players p USING (espn_id)
                        WHERE p.name LIKE 'Luka%' ORDER BY game_date DESC LIMIT 3"""):
    print(f"  {r['game_date'][:10]} {'vs' if r['home_away'] == 'home' else '@'} {r['opp_abbr']}"
          f" {r['result']} {r['score']}: {r['pts']} pts {r['reb']} reb {r['ast']} ast")
print("\nInjuries:")
for r in con.execute("SELECT * FROM injuries WHERE snapshot_date=?", (d,)):
    print(f"  team {r['team_id']}: {r['player']} - {r['status']}")

