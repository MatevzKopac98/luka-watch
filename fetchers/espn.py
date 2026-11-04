"""ESPN public API: standings, team stats, schedule, player stats, game logs, injuries."""
import re
import unicodedata

import requests

from .http import HEADERS, get_json

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
WEB = "https://site.web.api.espn.com/apis/common/v3"

LAKERS_ID = "13"


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _made_att(v):
    """'11.7-23.6' -> (11.7, 23.6)"""
    try:
        m, a = str(v).split("-")
        return float(m), float(a)
    except ValueError:
        return None, None


def ts_pct(pts, fga, fta):
    if not pts or not fga:
        return None
    return round(100 * pts / (2 * (fga + 0.44 * (fta or 0))), 1)


# ---------- teams ----------

def standings():
    """Returns {team_id: {...standings fields...}}."""
    data = get_json(STANDINGS)
    out = {}
    for conf in data["children"]:
        for e in conf["standings"]["entries"]:
            s = {x.get("type") or x.get("name"): x for x in e["stats"]}
            val = lambda k: s.get(k, {}).get("value")
            disp = lambda k: s.get(k, {}).get("displayValue")
            t = e["team"]
            out[t["id"]] = {
                "team_id": t["id"], "abbr": t["abbreviation"], "name": t["displayName"],
                "conference": conf["abbreviation"],
                "logo": (t.get("logos") or [{}])[0].get("href"),
                "wins": int(val("wins") or 0), "losses": int(val("losses") or 0),
                "win_pct": _num(val("winpercent")), "seed": int(val("playoffseed") or 0),
                "games_behind": disp("gamesbehind"), "streak": disp("streak"),
                "last10": disp("lasttengames"), "home": disp("home"), "road": disp("road"),
                "ppg": _num(val("avgpointsfor")), "opp_ppg": _num(val("avgpointsagainst")),
                "diff": _num(val("differential")),
            }
    return out


def team_stats(team_id):
    data = get_json(f"{SITE}/teams/{team_id}/statistics")
    flat = {}
    for c in data["results"]["stats"]["categories"]:
        for x in c["stats"]:
            flat[x["name"]] = _num(x.get("value", x.get("displayValue")))
    return flat


def team_rows():
    """Standings merged with box-score averages and estimated pace / ratings for all 30 teams."""
    rows = standings()
    for tid, row in rows.items():
        s = team_stats(tid)
        fga, oreb = s.get("avgFieldGoalsAttempted"), s.get("avgOffensiveRebounds")
        tov, fta = s.get("avgTurnovers"), s.get("avgFreeThrowsAttempted")
        poss = (fga - oreb + tov + 0.44 * fta) if None not in (fga, oreb, tov, fta) else None
        row.update({
            "fg_pct": s.get("fieldGoalPct"), "fg3_pct": s.get("threePointFieldGoalPct"),
            "ft_pct": s.get("freeThrowPct"), "reb": s.get("avgRebounds"), "oreb": oreb,
            "ast": s.get("avgAssists"), "tov": tov, "stl": s.get("avgSteals"), "blk": s.get("avgBlocks"),
            "pace": round(poss, 1) if poss else None,
        })
        if poss and row["ppg"] is not None:
            row["ortg"] = round(100 * row["ppg"] / poss, 1)
            row["drtg"] = round(100 * row["opp_ppg"] / poss, 1)
            row["net_rtg"] = round(row["ortg"] - row["drtg"], 1)
    return rows


def schedule(team_id=LAKERS_ID):
    data = get_json(f"{SITE}/teams/{team_id}/schedule")
    games = []
    for ev in data["events"]:
        c = ev["competitions"][0]
        side = {t["homeAway"]: t for t in c["competitors"]}

        def score(t):
            sc = t.get("score")
            return int(_num(sc.get("value") if isinstance(sc, dict) else sc)) if sc else None

        games.append({
            "event_id": ev["id"], "date_utc": ev["date"], "status": c["status"]["type"]["name"],
            "home_id": side["home"]["team"]["id"], "home_abbr": side["home"]["team"]["abbreviation"],
            "home_score": score(side["home"]),
            "away_id": side["away"]["team"]["id"], "away_abbr": side["away"]["team"]["abbreviation"],
            "away_score": score(side["away"]),
        })
    return games


def injuries(team_ids=None):
    """Injury report; team_ids=None returns every team."""
    data = get_json(f"{SITE}/injuries")
    out = []
    for team in data.get("injuries", []):
        if team_ids is not None and team.get("id") not in team_ids:
            continue
        for inj in team.get("injuries", []):
            athlete = inj.get("athlete") or {}
            link = " ".join(l.get("href", "") for l in athlete.get("links") or [])
            found = re.search(r"/id/(\d+)", link)              # the id only appears in the player-card link
            out.append({
                "team_id": team["id"], "player": athlete.get("displayName"),
                "athlete_id": found.group(1) if found else None,
                "status": inj.get("status"), "detail": inj.get("shortComment"),
            })
    return out


def team_roster(team_id):
    """Current roster: list of athlete ids."""
    return [p["athlete_id"] for p in team_roster_full(team_id)]


def team_roster_full(team_id, season=None):
    """Current roster with profile and salary (from ESPN's contract history on the roster feed)."""
    data = get_json(f"{SITE}/teams/{team_id}/roster")
    season = season or (data.get("season") or {}).get("year")
    out = []
    for a in data.get("athletes", []):
        if not a.get("id"):
            continue
        salaries = {(c.get("season") or {}).get("year"): c.get("salary") for c in a.get("contracts") or []}
        out.append({
            "team_id": str(team_id), "athlete_id": a["id"], "name": a.get("displayName"),
            "position": (a.get("position") or {}).get("abbreviation"), "age": a.get("age"),
            "height": a.get("displayHeight"), "weight": a.get("displayWeight"),
            "experience": (a.get("experience") or {}).get("years"), "jersey": a.get("jersey"),
            "headshot": (a.get("headshot") or {}).get("href"),
            "salary": salaries.get(season), "salary_last": salaries.get(season - 1) if season else None,
        })
    return out


CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"


def player_contract(athlete_id, season):
    """This season's contract (salary, years remaining) from ESPN's contract records, or None.

    One attempt only: many players simply have no record for the season, and retrying those is slow.
    """
    try:
        r = requests.get(f"{CORE}/athletes/{athlete_id}/contracts/{season}", headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        c = r.json()
    except (requests.RequestException, ValueError):
        return None
    return {"salary": c.get("salary"), "years_remaining": c.get("yearsRemaining"), "option_type": c.get("optionType")}


# ---------- players ----------

def _plain(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


def find_player(name):
    """Look up an ESPN player id by name (accent-insensitive)."""
    data = get_json(f"{WEB}/search", {"query": _plain(name), "type": "player", "limit": 5})
    items = [i for i in data.get("items", []) if i.get("league") == "nba"]
    for i in items:
        if _plain(i["displayName"]) == _plain(name):
            return i["id"]
    return items[0]["id"] if items else None


def player_profile(espn_id):
    a = get_json(f"{WEB}/sports/basketball/nba/athletes/{espn_id}")["athlete"]
    team = a.get("team") or {}
    return {
        "espn_id": espn_id, "name": a["displayName"], "team_abbr": team.get("abbreviation"),
        "team_id": team.get("id"), "position": (a.get("position") or {}).get("abbreviation"),
        "headshot": (a.get("headshot") or {}).get("href"),
    }


def player_season(espn_id, season_year):
    """Current-season per-game averages, or None if the player hasn't played this season."""
    data = get_json(f"{WEB}/sports/basketball/nba/athletes/{espn_id}/stats")
    cats = {c["name"]: c for c in data.get("categories", [])}

    def season_row(cat):
        for st in cats.get(cat, {}).get("statistics", []):
            if st.get("season", {}).get("year") == season_year:
                return dict(zip(cats[cat]["labels"], st["stats"]))
        return None

    avg = season_row("averages")
    if not avg:
        return None
    misc = season_row("miscellaneous") or {}
    fgm, fga = _made_att(avg["FG"])
    fg3m, fg3a = _made_att(avg["3PT"])
    ftm, fta = _made_att(avg["FT"])
    pts = _num(avg["PTS"])
    return {
        "espn_id": espn_id, "gp": int(_num(avg["GP"]) or 0), "min": _num(avg["MIN"]), "pts": pts,
        "reb": _num(avg["REB"]), "ast": _num(avg["AST"]), "stl": _num(avg["STL"]),
        "blk": _num(avg["BLK"]), "tov": _num(avg["TO"]),
        "fgm": fgm, "fga": fga, "fg_pct": _num(avg["FG%"]),
        "fg3m": fg3m, "fg3a": fg3a, "fg3_pct": _num(avg["3P%"]),
        "ftm": ftm, "fta": fta, "ft_pct": _num(avg["FT%"]),
        "ts_pct": ts_pct(pts, fga, fta),
        "dd2": int(_num(misc.get("DD2")) or 0), "td3": int(_num(misc.get("TD3")) or 0),
    }


RECORD_WORDS = re.compile(
    r"\b(record|only (player|the|one)|first (player|ever|time since)|since (19|20)\d\d|history|historic|all-time|"
    r"franchise|joins? (wilt|jordan|michael|kareem|oscar|lebron|kobe))", re.I)


def luka_record_news(limit=100):
    """ESPN NBA news items about Doncic that mention a record or historic feat."""
    data = get_json(f"{SITE}/news", {"limit": limit})
    out = []
    for a in data.get("articles", []):
        text = f"{a.get('headline', '')} {a.get('description', '')}"
        if re.search(r"Don[cč]i[cć]", text) and RECORD_WORDS.search(text):
            out.append({"published": a.get("published"), "headline": a.get("headline"),
                        "url": ((a.get("links") or {}).get("web") or {}).get("href")})
    return out


def player_career(espn_id):
    """Career regular-season totals (ESPN puts them in each category's 'totals')."""
    data = get_json(f"{WEB}/sports/basketball/nba/athletes/{espn_id}/stats")
    cats = {c["name"]: dict(zip(c["labels"], c.get("totals") or [])) for c in data.get("categories", [])}
    avg, tot, misc = cats.get("averages", {}), cats.get("totals", {}), cats.get("miscellaneous", {})
    return {
        "espn_id": espn_id, "gp": _num(avg.get("GP")), "pts": _num(tot.get("PTS")), "reb": _num(tot.get("REB")),
        "ast": _num(tot.get("AST")), "fg3m": _made_att(tot.get("3PT"))[0], "stl": _num(tot.get("STL")),
        "blk": _num(tot.get("BLK")), "dd2": _num(misc.get("DD2")), "td3": _num(misc.get("TD3")),
    }


# The splits feed uses older team codes; map them to the ones the rest of ESPN uses
SPLIT_ABBR = {"PHO": "PHX", "UTA": "UTAH", "WAS": "WSH", "NOP": "NO", "GSW": "GS", "SAS": "SA",
              "NYK": "NY", "BRK": "BKN", "NJN": "BKN", "CHO": "CHA"}


def player_vs_opponents(espn_id, season):
    """Per-opponent averages for one season: [{opp_abbr, gp, min, pts, ...}]."""
    data = get_json(f"{WEB}/sports/basketball/nba/athletes/{espn_id}/splits", {"season": season})
    names = data.get("names", [])
    cat = next((c for c in data.get("splitCategories", []) if c.get("name") == "byOpponent"), {})
    out = []
    for s in cat.get("splits", []):
        v = dict(zip(names, s.get("stats", [])))
        fgm, fga = _made_att(v.get("avgFieldGoalsMade-avgFieldGoalsAttempted"))
        fg3m, fg3a = _made_att(v.get("avgThreePointFieldGoalsMade-avgThreePointFieldGoalsAttempted"))
        ftm, fta = _made_att(v.get("Free Throws Made-Attempted Per Game"))
        abbr = (s.get("abbreviation") or "").replace("vs ", "").strip()
        out.append({
            "season": season, "opp_abbr": SPLIT_ABBR.get(abbr, abbr),
            "gp": _num(v.get("gamesPlayed")), "min": _num(v.get("avgMinutes")), "pts": _num(v.get("avgPoints")),
            "reb": _num(v.get("avgRebounds")), "ast": _num(v.get("avgAssists")),
            "fgm": fgm, "fga": fga, "fg3m": fg3m, "fg3a": fg3a, "ftm": ftm, "fta": fta,
        })
    return out


def player_gamelog(espn_id, season=None):
    """Regular-season game log (current season unless a season year is given)."""
    data = get_json(f"{WEB}/sports/basketball/nba/athletes/{espn_id}/gamelog",
                    {"season": season} if season else None)
    labels, meta = data.get("labels", []), data.get("events", {})
    rows = []
    for stype in data.get("seasonTypes", []):
        if "Regular Season" not in stype.get("displayName", ""):
            continue
        for cat in stype.get("categories", []):
            for ev in cat.get("events", []):
                s = dict(zip(labels, ev["stats"]))
                m = meta.get(ev["eventId"], {})
                fgm, fga = _made_att(s.get("FG"))
                fg3m, fg3a = _made_att(s.get("3PT"))
                ftm, fta = _made_att(s.get("FT"))
                rows.append({
                    "espn_id": espn_id, "event_id": ev["eventId"], "game_date": m.get("gameDate"),
                    "opp_abbr": (m.get("opponent") or {}).get("abbreviation"),
                    "home_away": "away" if m.get("atVs") == "@" else "home",
                    "result": m.get("gameResult"), "score": m.get("score"),
                    "min": _num(s.get("MIN")), "pts": _num(s.get("PTS")), "reb": _num(s.get("REB")),
                    "ast": _num(s.get("AST")), "stl": _num(s.get("STL")), "blk": _num(s.get("BLK")),
                    "tov": _num(s.get("TO")), "fgm": fgm, "fga": fga, "fg3m": fg3m, "fg3a": fg3a,
                    "ftm": ftm, "fta": fta,
                })
    return rows


def league_players(season_year):
    """Per-game stats for every qualified player in the league (used for league ranks)."""
    data = get_json(f"{WEB}/sports/basketball/nba/statistics/byathlete",
                    {"region": "us", "lang": "en", "contentorigin": "espn", "isqualified": "true",
                     "page": 1, "limit": 1000, "sort": "offensive.avgPoints:desc",
                     "season": season_year, "seasontype": 2})
    names = {c["name"]: c["names"] for c in data.get("categories", [])}
    rows = []
    for a in data.get("athletes", []):
        s = {}
        for c in a["categories"]:
            s.update({k: _num(v) for k, v in zip(names.get(c["name"], []), c.get("totals", []))})
        pts, fga, fta = s.get("avgPoints"), s.get("avgFieldGoalsAttempted"), s.get("avgFreeThrowsAttempted")
        rows.append({
            "espn_id": a["athlete"]["id"], "name": a["athlete"]["displayName"],
            "team_abbr": (a["athlete"].get("teamShortName") or (a["athlete"].get("team") or {}).get("abbreviation")),
            "gp": s.get("gamesPlayed"), "min": s.get("avgMinutes"), "pts": pts, "reb": s.get("avgRebounds"),
            "ast": s.get("avgAssists"), "stl": s.get("avgSteals"), "blk": s.get("avgBlocks"),
            "tov": s.get("avgTurnovers"), "fga": fga, "fg_pct": s.get("fieldGoalPct"),
            "fg3m": s.get("avgThreePointFieldGoalsMade"), "fg3a": s.get("avgThreePointFieldGoalsAttempted"),
            "fg3_pct": s.get("threePointFieldGoalPct"), "fta": fta, "ft_pct": s.get("freeThrowPct"),
            "ts_pct": ts_pct(pts, fga, fta), "dd2": s.get("doubleDouble"), "td3": s.get("tripleDouble"),
        })
    return rows


# ---------- games: scoreboard & box scores ----------

TEAM_BOX = {   # ESPN stat name -> our column (made-attempted pairs are split below)
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
    "totalRebounds": "reb", "offensiveRebounds": "oreb", "defensiveRebounds": "dreb",
    "assists": "ast", "steals": "stl", "blocks": "blk", "totalTurnovers": "tov", "fouls": "pf",
    "turnoverPoints": "pts_off_tov",     # points this team CONCEDED after its own turnovers "fastBreakPoints": "fastbreak", "pointsInPaint": "paint",
    "largestLead": "largest_lead", "leadChanges": "lead_changes", "leadPercentage": "lead_pct",
}
PLAYER_BOX = {
    "minutes": "min", "points": "pts", "rebounds": "reb", "assists": "ast", "turnovers": "tov",
    "steals": "stl", "blocks": "blk", "offensiveRebounds": "oreb", "defensiveRebounds": "dreb",
    "fouls": "pf", "plusMinus": "plus_minus",
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
}


def _box(mapping, name, value, out):
    col = mapping.get(name)
    if isinstance(col, tuple):
        out[col[0]], out[col[1]] = _made_att(value)
    elif col:
        out[col] = _num(str(value).replace("+", ""))


def scoreboard(us_date):
    """All games on a US calendar date ('YYYYMMDD'): [{event_id, status}]."""
    data = get_json(f"{SITE}/scoreboard", {"dates": us_date})
    return [{"event_id": e["id"], "status": e["competitions"][0]["status"]["type"]["name"]}
            for e in data.get("events", [])]


def raw_summary(event_id):
    return get_json(f"{SITE}/summary", {"event": event_id})


def game_summary(event_id, us_date, s=None):
    """Box score of one game: (info row, [team rows], [player rows]). Pass s to reuse a fetched summary."""
    s = s or raw_summary(event_id)
    comp = s["header"]["competitions"][0]
    side = {c["homeAway"]: c for c in comp["competitors"]}
    lines = lambda c: ",".join(str(l.get("displayValue", "")) for l in c.get("linescores", []))
    plays = s.get("plays") or []
    end = next((p.get("wallclock") for p in reversed(plays) if p.get("text") == "End of Game"), None)
    video = next((v for v in s.get("videos", []) if "Game Highlights" in (v.get("headline") or "")), None)
    article = s.get("article") or {}
    info = {
        "event_id": event_id, "us_date": us_date, "start_utc": comp["date"], "end_utc": end,
        "status": comp["status"]["type"]["name"],
        "home_id": side["home"]["team"]["id"], "home_abbr": side["home"]["team"]["abbreviation"],
        "home_score": int(_num(side["home"].get("score")) or 0), "home_lines": lines(side["home"]),
        "away_id": side["away"]["team"]["id"], "away_abbr": side["away"]["team"]["abbreviation"],
        "away_score": int(_num(side["away"].get("score")) or 0), "away_lines": lines(side["away"]),
        "headline": article.get("headline"),
        "highlights_url": ((video or {}).get("links") or {}).get("web", {}).get("href"),
    }
    teams = []
    for t in s.get("boxscore", {}).get("teams", []):
        row = {"event_id": event_id, "team_id": t["team"]["id"], "abbr": t["team"]["abbreviation"]}
        for x in t.get("statistics", []):
            _box(TEAM_BOX, x["name"], x.get("displayValue"), row)
        teams.append(row)
    players = []
    for p in s.get("boxscore", {}).get("players", []):
        st = (p.get("statistics") or [{}])[0]
        keys = st.get("keys", [])
        for a in st.get("athletes", []):
            if a.get("didNotPlay") or not a.get("stats") or not (a.get("athlete") or {}).get("id"):
                continue
            row = {"event_id": event_id, "team_id": p["team"]["id"], "athlete_id": a["athlete"]["id"],
                   "name": a["athlete"]["displayName"], "starter": int(bool(a.get("starter")))}
            for k, v in zip(keys, a["stats"]):
                _box(PLAYER_BOX, k, v, row)
            players.append(row)
    return info, teams, players


def season_year():
    """ESPN labels a season by its ending year (2026-27 -> 2027)."""
    return get_json(f"{SITE}/teams/{LAKERS_ID}/schedule")["season"]["year"]
