"""Daily update: fetch everything and store today's snapshot. Run: python scripts/update.py"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect, upsert                      # noqa: E402
from fetchers import espn, odds                     # noqa: E402

TZ = ZoneInfo("Europe/Ljubljana")

# Core comparison group, always shown (ESPN ids are looked up by name when missing)
CORE_PLAYERS = ["Luka Doncic", "Nikola Jokic", "Shai Gilgeous-Alexander",
                "Giannis Antetokounmpo", "Victor Wembanyama"]
MVP_TOP_N = 5          # also track everyone in the top N of the MVP odds
GAME_NIGHTS_BACK = 3   # box scores: re-check the last N nights for games not yet stored


def log(msg):
    print(f"[{datetime.now(TZ):%H:%M:%S}] {msg}", flush=True)


def resolve_player(con, name):
    """Map a display name to an ESPN id, caching it in the players table."""
    plain = espn._plain(name)
    for row in con.execute("SELECT espn_id, name FROM players"):
        if espn._plain(row["name"]) == plain:
            return row["espn_id"]
    return espn.find_player(name)


def main():
    today = datetime.now(TZ).date().isoformat()
    con = connect()
    errors = []
    year = espn.season_year()
    log(f"Season {year - 1}-{str(year)[2:]}, snapshot {today}")

    # 1. Teams: standings + stats for all 30
    try:
        teams = espn.team_rows()
        for row in teams.values():
            upsert(con, "team_season", {"snapshot_date": today, **row})
        log(f"Teams: {len(teams)}")
    except Exception as e:
        errors.append(f"teams: {e}")

    # 2. Lakers schedule
    try:
        games = espn.schedule()
        for g in games:
            upsert(con, "games", g)
        log(f"Lakers schedule: {len(games)} games")
    except Exception as e:
        errors.append(f"schedule: {e}")
        games = []

    # 3. MVP odds (Polymarket)
    mvp = []
    try:
        mvp = odds.polymarket_mvp(f"nba-{year - 1}-{str(year)[2:]}-mvp")
        for r in mvp:
            upsert(con, "mvp_odds", {"snapshot_date": today, **r})
        log(f"Polymarket MVP odds: {len(mvp)} players")
    except Exception as e:
        errors.append(f"Polymarket: {e}")

    # 4. Players: core group + MVP favourites
    favourites = [r["player_name"] for r in sorted(mvp, key=lambda r: -r["prob"])[:MVP_TOP_N]]
    names = list({espn._plain(n): n for n in reversed(CORE_PLAYERS + favourites)}.values())[::-1]
    core = {espn._plain(n) for n in CORE_PLAYERS}
    for name in names:
        try:
            pid = resolve_player(con, name)
            if not pid:
                errors.append(f"player not found: {name}")
                continue
            profile = espn.player_profile(pid)
            profile["tracked"] = int(espn._plain(name) in core)
            upsert(con, "players", profile)
            season = espn.player_season(pid, year)
            if season:
                upsert(con, "player_season", {"snapshot_date": today, **season})
            for g in espn.player_gamelog(pid):
                upsert(con, "player_games", g)
            log(f"Player: {profile['name']} ({profile['team_abbr']})"
                f" {season['pts'] if season else '-'} ppg")
        except Exception as e:
            errors.append(f"player {name}: {e}")

    # 5. Box scores: every finished game of the last few nights (US dates), plus the Lakers' last game
    try:
        have = {r[0] for r in con.execute("SELECT event_id FROM game_info WHERE status='STATUS_FINAL'")}
        local_today = datetime.now(TZ).date()
        targets = []
        for back in range(GAME_NIGHTS_BACK, 0, -1):
            us_date = (local_today - timedelta(days=back)).strftime("%Y%m%d")
            targets += [(g["event_id"], us_date) for g in espn.scoreboard(us_date)
                        if g["status"] == "STATUS_FINAL" and g["event_id"] not in have]
        lal_final = [g for g in games if g["status"] == "STATUS_FINAL"]
        if lal_final and lal_final[-1]["event_id"] not in have | {t[0] for t in targets}:
            g = lal_final[-1]
            us = datetime.fromisoformat(g["date_utc"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
            targets.append((g["event_id"], us.strftime("%Y%m%d")))
        for event_id, us_date in targets:
            info, team_rows, player_rows = espn.game_summary(event_id, us_date)
            upsert(con, "game_info", info)
            for r in team_rows:
                upsert(con, "game_team_box", r)
            con.execute("DELETE FROM game_players WHERE event_id = ?", (event_id,))
            for r in player_rows:
                upsert(con, "game_players", r)
        log(f"Box scores: {len(targets)} new games")
    except Exception as e:
        errors.append(f"box scores: {e}")

    # 6. Injuries for the Lakers and their next opponent
    try:
        upcoming = [g for g in games if g["status"] != "STATUS_FINAL"]
        ids = {espn.LAKERS_ID}
        if upcoming:
            g = upcoming[0]
            ids.add(g["away_id"] if g["home_id"] == espn.LAKERS_ID else g["home_id"])
        con.execute("DELETE FROM injuries WHERE snapshot_date = ?", (today,))
        for inj in espn.injuries(ids):
            upsert(con, "injuries", {"snapshot_date": today, **inj})
        log(f"Injuries for teams {sorted(ids)}")
    except Exception as e:
        errors.append(f"injuries: {e}")

    con.commit()
    con.close()

    # 7. Rebuild the website from the fresh data
    try:
        from scripts.build_site import build
        build()
    except Exception as e:
        errors.append(f"build_site: {e}")

    for e in errors:
        log(f"ERROR {e}")
    log("Done" if not errors else f"Done with {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
