"""When do games actually end (Ljubljana time)? Used to tune the morning schedule.
Run: python scripts/end_times.py   (git pull first to get the latest data)"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import connect  # noqa: E402

TZ = ZoneInfo("Europe/Ljubljana")
CHECKS = ["06:40", "07:00", "07:20", "07:35", "08:00"]

con = connect()
nights = {}
for r in con.execute("SELECT us_date, end_utc, home_abbr, away_abbr FROM game_info WHERE end_utc IS NOT NULL"):
    end = datetime.fromisoformat(r["end_utc"].replace("Z", "+00:00")).astimezone(TZ)
    nights.setdefault(r["us_date"], []).append((end, f"{r['away_abbr']}@{r['home_abbr']}"))

print(f"{'US night':<10} {'games':>5}  {'last game ended':<22} " + " ".join(f"{c:>6}" for c in CHECKS))
all_done = {c: 0 for c in CHECKS}
for night in sorted(nights):
    games = nights[night]
    last_end, last_game = max(games)
    row = []
    for c in CHECKS:
        h, m = map(int, c.split(":"))
        done = sum(1 for e, _ in games if (e.hour, e.minute) <= (h, m) or e.date() < last_end.date())
        all_done[c] += done == len(games)
        row.append(f"{done}/{len(games)}".rjust(6))
    print(f"{night:<10} {len(games):>5}  {last_end:%H:%M} ({last_game})".ljust(41) + " ".join(row))

n = len(nights)
if n:
    print("\nMornings with every game finished by:")
    for c in CHECKS:
        print(f"  {c}  {all_done[c]}/{n} ({all_done[c] / n:.0%})")
