"""Betting run: unlock the private database, grade, recalibrate, predict, build the locked page, lock again.

Called by update.py (morning: grading) and by the odds capture job (afternoon: picks once props are up).
Run on its own: python scripts/run_betting.py   (needs the BETTING_PASSWORD environment variable)
"""
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db import connect as connect_site    # noqa: E402
from fetchers import espn                  # noqa: E402
from fetchers.http import get_json         # noqa: E402
from model import betting, crypto          # noqa: E402

DATA = ROOT / "data"
ENCRYPTED_DB = DATA / "betting.sqlite.enc"
PAGE_PAYLOAD = DATA / "betting_page.json"
SALT_FILE = DATA / "betting_page.salt"
NY = ZoneInfo("America/New_York")


def schedule_for(us_date):
    """{event_id: {tip_utc, home_id, away_id, label, started}} from the ESPN scoreboard."""
    out = {}
    for e in get_json(f"{espn.SITE}/scoreboard", {"dates": us_date}).get("events", []):
        c = e["competitions"][0]
        side = {t["homeAway"]: t["team"] for t in c["competitors"]}
        out[e["id"]] = {"tip_utc": e["date"], "home_id": side["home"]["id"], "away_id": side["away"]["id"],
                        "label": f"{side['away']['abbreviation']} @ {side['home']['abbreviation']}",
                        "started": c["status"]["type"]["name"] != "STATUS_SCHEDULED"}
    return out


def run(log=print):
    pw = crypto.password()
    if not pw:
        log("Betting: BETTING_PASSWORD not set; page stays 'not configured'")
        return None
    if ENCRYPTED_DB.exists():
        try:
            crypto.decrypt_file(ENCRYPTED_DB, betting.DB_PATH, pw)
        except Exception:                                    # password changed: keep the old file, start fresh
            kept = ENCRYPTED_DB.with_name(f"betting.sqlite.enc.old-{datetime.now():%Y%m%d%H%M}")
            ENCRYPTED_DB.rename(kept)
            log(f"Betting: could not open the saved history with this password; kept it as {kept.name} "
                f"and started a fresh record")
    try:
        con = betting.connect()
        season = espn.season_year()
        graded = betting.grade(con, season)
        learning = betting.recalibrate(con)
        us_date = datetime.now(NY).strftime("%Y%m%d")
        injured = {r["athlete_id"]: r["status"] for r in espn.injuries(None)
                   if r.get("athlete_id") and (r.get("status") or "").lower() in ("out", "doubtful")}
        with connect_site() as site:
            result = betting.predict(con, us_date, season, site, injured, schedule_for(us_date),
                                     datetime.now(ZoneInfo("Europe/Ljubljana")).strftime("%H:%M"))
        from scripts.betting_page import render
        html = render(con)
        if not SALT_FILE.exists():
            SALT_FILE.write_bytes(os.urandom(16))
        PAGE_PAYLOAD.write_text(crypto.page_payload(html, pw, SALT_FILE.read_bytes()), encoding="utf-8")
        con.close()
        log(f"Betting: graded {graded}, {result}, learning: {learning.get('status')}")
        return result
    finally:
        if betting.DB_PATH.exists():                         # never leave the private database unencrypted
            crypto.encrypt_file(betting.DB_PATH, ENCRYPTED_DB, pw)
            betting.DB_PATH.unlink()


if __name__ == "__main__":
    run()
