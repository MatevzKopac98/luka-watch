"""Thin out old odds/props snapshots so the data branch doesn't grow ~2.5 MB a day.

The betting model reads, for each US date: the first snapshot (opening lines), the last one (current lines)
and, when grading, the last one before each game's tip-off (closing lines). With captures at 10:23, 14:23,
18:13 and 21:13 New York time, the last two of a day hold the closing lines of 7 pm and 10 pm games.

Retention per US date:
  - the last KEEP_ALL_DAYS days: every snapshot (picks are still being made and graded)
  - up to KEEP_THIN_DAYS days:   first + last two (opening and closing lines, for re-checking the model)
  - older:                       the last one only
Run: python scripts/prune.py
"""
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DAILY = Path(__file__).resolve().parent.parent / "data" / "daily"
KEEP_ALL_DAYS = 3
KEEP_THIN_DAYS = 30
NAME = re.compile(r"^(\d{8})_(\d{4})\.csv\.gz$")


def keep(files, age_days):
    """Which of one US date's snapshot files (sorted oldest first) to keep at this age."""
    if age_days <= KEEP_ALL_DAYS:
        return list(files)
    if age_days <= KEEP_THIN_DAYS:
        return list(dict.fromkeys(files[:1] + files[-2:]))
    return files[-1:]


def prune(daily=DAILY, today=None, dry_run=False):
    """Delete surplus snapshots under daily/odds and daily/props. Returns (files removed, bytes freed)."""
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    removed = freed = 0
    for kind in ("odds", "props"):
        by_date = {}
        for f in sorted((Path(daily) / kind).glob("*.csv.gz")):
            m = NAME.match(f.name)
            if m:
                by_date.setdefault(m.group(1), []).append(f)
        for us_date, files in by_date.items():
            age = (today - date(int(us_date[:4]), int(us_date[4:6]), int(us_date[6:]))).days
            kept = set(keep(files, age))
            for f in files:
                if f not in kept:
                    freed += f.stat().st_size
                    removed += 1
                    if not dry_run:
                        f.unlink()
    return removed, freed


if __name__ == "__main__":
    n, b = prune(dry_run="--dry-run" in sys.argv)
    print(f"{'Would remove' if '--dry-run' in sys.argv else 'Removed'} {n} snapshots ({b / 1e6:.1f} MB)")
