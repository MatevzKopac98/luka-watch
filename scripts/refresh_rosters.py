"""Fetch today's rosters (profiles, salaries) and stale contract details. Run: python scripts/refresh_rosters.py

The daily update does this too; this is for a one-off refresh without the full update.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import connect, upsert              # noqa: E402
from fetchers import espn                   # noqa: E402
from scripts.update import refresh_contracts  # noqa: E402

if __name__ == "__main__":
    t0 = time.time()
    con = connect()
    year = espn.season_year()
    teams = {r["team_id"] for r in con.execute("SELECT DISTINCT team_id FROM team_season")}
    con.execute("DELETE FROM roster_players")
    n = 0
    for t in teams:
        for e in espn.team_roster_full(t, year):
            upsert(con, "roster_players", e)
            n += 1
    con.commit()
    refreshed = refresh_contracts(con, year)
    con.commit()
    have = con.execute("SELECT COUNT(*), SUM(salary IS NOT NULL), SUM(years_remaining IS NOT NULL) FROM contracts").fetchone()
    print(f"{n} roster players, {refreshed} contracts refreshed in {time.time() - t0:.0f}s; "
          f"contracts with salary {have[1]}/{have[0]}, with years left {have[2]}")
