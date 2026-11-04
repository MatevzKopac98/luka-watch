"""Snapshot DraftKings lines and props for tonight's games. Run: python scripts/capture_odds.py

Runs several times a day (.github/workflows/odds.yml) so line movement and closing lines are kept.
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fetchers import odds_capture  # noqa: E402

if __name__ == "__main__":
    now = datetime.now(ZoneInfo("Europe/Ljubljana"))
    us_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    n, o, p, failed = odds_capture.capture(us_date, now.isoformat(timespec="minutes"), now.strftime("%H%M"))
    print(f"US date {us_date}: {n} upcoming games, {o} game lines, {p} props")
    for f in failed:
        print("FAILED", f)
