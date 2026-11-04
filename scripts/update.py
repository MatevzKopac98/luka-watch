"""Daily update: fetch everything and store today's snapshot. Run: python scripts/update.py"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect, upsert                      # noqa: E402
from fetchers import espn, odds, odds_capture       # noqa: E402
from fetchers.pbp import build_stints               # noqa: E402
from model import defense                           # noqa: E402
from scripts.backfill_history import HISTORY, append_rows  # noqa: E402

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


LUKA_FIRST_SEASON = 2019   # 2018-19 rookie season (ESPN labels seasons by their ending year)


def luka_extras(con, pid, year, today):
    """Career totals (milestones), last season's game log and per-opponent splits (matchup history)."""
    upsert(con, "player_career", {"snapshot_date": today, **espn.player_career(pid)})
    if not con.execute("SELECT 1 FROM luka_prev_games WHERE season = ? LIMIT 1", (year - 1,)).fetchone():
        for g in espn.player_gamelog(pid, year - 1):
            upsert(con, "luka_prev_games", {"season": year - 1, **g})
    stored = {r[0] for r in con.execute("SELECT DISTINCT season FROM luka_vs_opp")}
    for season in range(LUKA_FIRST_SEASON, year + 1):
        if season >= year - 1 or season not in stored:     # old seasons never change; refresh the last two
            for r in espn.player_vs_opponents(pid, season):
                upsert(con, "luka_vs_opp", r)


def defense_update(con, year, today, rosters, out_ids):
    """Rate every player's defense from stints, project each team's defense from its current roster."""
    ratings, league = defense.player_ratings(year)
    info = defense.player_info(year)
    con.execute("DELETE FROM player_defense")
    for pid, r in ratings.items():
        i = info.get(pid, {})
        upsert(con, "player_defense", {**r, "name": i.get("name"), "team_id": i.get("team_id")})
    minutes = {}
    for pid, i in info.items():                              # expected minutes: this season, else last season
        for season in (year, year - 1):
            if i["gp"].get(season, 0) >= 3:
                minutes[pid] = i["min"][season] / i["gp"][season]
                break
    current = defense.team_ratings_by_season(year)            # this season so far, same method as projections
    gp = {r["team_id"]: r["wins"] + r["losses"] for r in
          con.execute("SELECT team_id, wins, losses FROM team_season WHERE snapshot_date=?", (today,))}
    actual = {t: (*current[t], gp.get(t, 0)) for t in current}
    last = defense.team_ratings_by_season(year - 1)
    proj = defense.team_projection(ratings, league, rosters, minutes, out_ids, actual)
    for team, p in proj.items():
        lo, ld = last.get(team, (None, None))
        upsert(con, "team_projection", {"snapshot_date": today, "team_id": team, "league_rtg": round(league, 1),
                                        "last_season_ortg": lo, "last_season_drtg": ld, **p})
    return len(ratings), len(proj)


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
            if espn._plain(name) == espn._plain(CORE_PLAYERS[0]):
                luka_extras(con, pid, year, today)
            log(f"Player: {profile['name']} ({profile['team_abbr']})"
                f" {season['pts'] if season else '-'} ppg")
        except Exception as e:
            errors.append(f"player {name}: {e}")

    # 4b. Whole-league player stats (for Luka's league ranks)
    try:
        league = espn.league_players(year)
        con.execute("DELETE FROM league_players WHERE snapshot_date = ?", (today,))
        for r in league:
            upsert(con, "league_players", {"snapshot_date": today, **r})
        log(f"League players: {len(league)}")
    except Exception as e:
        errors.append(f"league players: {e}")

    # 5. Box scores: every finished game of the season (US dates), plus the Lakers' last game.
    #    Nights that are complete and older than GAME_NIGHTS_BACK are remembered and skipped.
    try:
        have = {r[0] for r in con.execute("SELECT event_id FROM game_info WHERE status='STATUS_FINAL'")}
        done = {r[0] for r in con.execute("SELECT us_date FROM nights_done")}
        local_today = datetime.now(TZ).date()
        first = min((g["date_utc"] for g in games), default=None)
        start = (datetime.fromisoformat(first.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date()
                 if first else local_today - timedelta(days=GAME_NIGHTS_BACK))
        targets, day = [], start
        while day < local_today:
            us_date = day.strftime("%Y%m%d")
            recent = (local_today - day).days <= GAME_NIGHTS_BACK
            if us_date not in done or recent:
                night = espn.scoreboard(us_date)
                targets += [(g["event_id"], us_date) for g in night
                            if g["status"] == "STATUS_FINAL" and g["event_id"] not in have]
                if not recent and all(g["status"] in ("STATUS_FINAL", "STATUS_POSTPONED", "STATUS_CANCELED")
                                      for g in night):
                    con.execute("INSERT OR IGNORE INTO nights_done (us_date) VALUES (?)", (us_date,))
            day += timedelta(days=1)
        lal_final = [g for g in games if g["status"] == "STATUS_FINAL"]
        if lal_final and lal_final[-1]["event_id"] not in have | {t[0] for t in targets}:
            g = lal_final[-1]
            us = datetime.fromisoformat(g["date_utc"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
            targets.append((g["event_id"], us.strftime("%Y%m%d")))
        season_dir = HISTORY / str(year)
        season_dir.mkdir(parents=True, exist_ok=True)
        done_file = season_dir / "_done.txt"
        hist_done = set(done_file.read_text().split()) if done_file.exists() else set()
        for event_id, us_date in targets:
            raw = espn.raw_summary(event_id)
            info, team_rows, player_rows = espn.game_summary(event_id, us_date, raw)
            upsert(con, "game_info", info)
            for r in team_rows:
                upsert(con, "game_team_box", r)
            con.execute("DELETE FROM game_players WHERE event_id = ?", (event_id,))
            for r in player_rows:
                upsert(con, "game_players", r)
            if event_id not in hist_done:                     # season files feed the lineup/defense model
                stints, quality = build_stints(raw)
                append_rows(season_dir / "games.csv.gz", [{**info, "season": year, "lineup_quality": round(quality, 3)}])
                append_rows(season_dir / "team_box.csv.gz", team_rows)
                append_rows(season_dir / "players.csv.gz", player_rows)
                append_rows(season_dir / "stints.csv.gz", stints)
                with open(done_file, "a") as f:
                    f.write(event_id + "\n")
                hist_done.add(event_id)
        log(f"Box scores: {len(targets)} new games")
    except Exception as e:
        errors.append(f"box scores: {e}")

    # 5b. Record headlines about Luka (ESPN news keeps only recent items, so we accumulate them)
    try:
        news = [n for n in espn.luka_record_news() if n["url"]]
        for n in news:
            upsert(con, "luka_news", n)
        log(f"Luka record headlines: {len(news)} found")
    except Exception as e:
        errors.append(f"news: {e}")

    # 6. Injuries for every team (the roster projection and the betting model need all of them)
    try:
        con.execute("DELETE FROM injuries WHERE snapshot_date = ?", (today,))
        injured = espn.injuries(None)
        for inj in injured:
            upsert(con, "injuries", {"snapshot_date": today, **inj})
        log(f"Injuries: {len(injured)} listed")
    except Exception as e:
        errors.append(f"injuries: {e}")

    # 6b. Odds and props for tonight's games (more captures run through the day: capture_odds.py)
    try:
        now = datetime.now(TZ)
        n, o, p, failed = odds_capture.capture(datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d"),
                                               now.isoformat(timespec="minutes"), now.strftime("%H%M"))
        log(f"Odds capture: {n} games, {o} game lines, {p} props" + (f", {len(failed)} failed" if failed else ""))
    except Exception as e:
        errors.append(f"odds capture: {e}")

    # 6c. Player defensive ratings and roster-projected team defense
    try:
        rosters = {t: espn.team_roster(t) for t in {r["team_id"] for r in
                                                    con.execute("SELECT team_id FROM team_season WHERE snapshot_date=?", (today,))}}
        out_ids = {r["athlete_id"] for r in injured if (r.get("status") or "").lower() == "out" and r.get("athlete_id")}
        n_players, n_teams = defense_update(con, year, today, rosters, out_ids)
        log(f"Defense model: {n_players} players rated, {n_teams} team projections")
    except Exception as e:
        errors.append(f"defense model: {e}")

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
