"""Morning sanity checks: did each source deliver what it should? Written to data/status.json for the site.

The site shows a banner when the last morning update is too old (checked in the reader's browser, so it
also works when no update ran at all) or when a source failed.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STATUS = Path(__file__).resolve().parent.parent / "data" / "status.json"
TZ = ZoneInfo("Europe/Ljubljana")


def checks(con, today, last_night_finals=None, odds_counts=None, betting_expected=False, betting_page=None):
    """Plain-language warnings about suspicious data (empty is good).

    last_night_finals: event ids ESPN lists as final for last night (US date); odds_counts: (games, lines, props)
    from this morning's capture; betting_page: path of the encrypted page payload when betting is configured.
    """
    warn = []

    def one(sql, *args):
        return con.execute(sql, args).fetchone()[0]

    n = one("SELECT COUNT(*) FROM team_season WHERE snapshot_date=?", today)
    if n != 30:
        warn.append(f"Standings: {n} of 30 teams today")
    if one("SELECT COUNT(*) FROM games") == 0:
        warn.append("Lakers schedule is empty")
    n = one("SELECT COUNT(*) FROM mvp_odds WHERE snapshot_date=?", today)
    if n < 3:
        warn.append(f"MVP odds: {n} players today (Polymarket)")
    n = one("SELECT COUNT(*) FROM player_season WHERE snapshot_date=?", today)
    if n < 5:
        warn.append(f"Player stats: {n} players today")
    if one("SELECT COUNT(*) FROM injuries WHERE snapshot_date=?", today) == 0:
        warn.append("Injury report is empty")
    if last_night_finals:
        have = {r[0] for r in con.execute(
            f"SELECT event_id FROM game_info WHERE event_id IN ({','.join('?' * len(last_night_finals))})",
            list(last_night_finals))}
        missing = len(set(last_night_finals) - have)
        if missing:
            warn.append(f"Box scores: {missing} of last night's {len(last_night_finals)} games missing")
    if odds_counts and odds_counts[0] and not odds_counts[1]:
        warn.append(f"Odds: {odds_counts[0]} games tonight but no DraftKings lines captured")
    if betting_expected and betting_page is not None:
        fresh = betting_page.exists() and (
            datetime.now().timestamp() - betting_page.stat().st_mtime < 3 * 3600)
        if not fresh:
            warn.append("Betting page was not refreshed")
    return warn


def write(errors, warnings, path=STATUS):
    data = {"daily_run": datetime.now(TZ).isoformat(timespec="seconds"), "errors": errors, "warnings": warnings}
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def read(path=STATUS):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stale_after_hours():
    """The morning run finishes by ~08:30; a page older than this has missed at least one morning."""
    return 26
