"""ESPN public API: standings, team stats, schedule, player stats, game logs, injuries."""
import unicodedata
from .http import get_json

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


def injuries(team_ids):
    data = get_json(f"{SITE}/injuries")
    out = []
    for team in data.get("injuries", []):
        if team.get("id") not in team_ids:
            continue
        for inj in team.get("injuries", []):
            out.append({
                "team_id": team["id"], "player": inj.get("athlete", {}).get("displayName"),
                "status": inj.get("status"), "detail": inj.get("shortComment"),
            })
    return out


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


def player_gamelog(espn_id):
    """Regular-season game log for the current season."""
    data = get_json(f"{WEB}/sports/basketball/nba/athletes/{espn_id}/gamelog")
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


def season_year():
    """ESPN labels a season by its ending year (2026-27 -> 2027)."""
    return get_json(f"{SITE}/teams/{LAKERS_ID}/schedule")["season"]["year"]
