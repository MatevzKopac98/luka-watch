"""Keep season numbers current from our own box scores.

ESPN's season-average, league-leader, career and split feeds update about a day after the game, while the
box scores we store every morning already include last night. After the box scores are in, the daily update
calls refresh(): wherever a feed counts fewer games than the box scores, its numbers are replaced (or, for
career totals, topped up with the missing games). Feeds that are already current are left alone.
"""
from collections import defaultdict

from model.defense import HISTORY, _f, _read

COUNT = ("min", "pts", "reb", "oreb", "ast", "stl", "blk", "tov", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta")


def _rows(season):
    d = HISTORY / str(season)
    games = {g["event_id"]: g for g in _read(d / "games.csv.gz") if g.get("status") == "STATUS_FINAL"}
    players = [p for p in _read(d / "players.csv.gz") if p["event_id"] in games and _f(p["min"]) > 0]
    return games, players


def _doubles(p):
    tens = sum(_f(p[k]) >= 10 for k in ("pts", "reb", "ast", "stl", "blk"))
    return tens >= 2, tens >= 3


def player_lines(season):
    """{athlete_id: totals over every game he played, plus gp, dd2, td3 and his games in date order}."""
    games, players = _rows(season)
    out = {}
    for p in sorted(players, key=lambda p: games[p["event_id"]]["us_date"]):
        t = out.setdefault(p["athlete_id"], {"gp": 0, "dd2": 0, "td3": 0, "games": [], **{k: 0.0 for k in COUNT}})
        t["gp"] += 1
        for k in COUNT:
            t[k] += _f(p.get(k))
        dd, td = _doubles(p)
        t["dd2"] += dd
        t["td3"] += td
        t["games"].append(p)
    return out


def _pct(m, a):
    return round(100 * m / a, 1) if a else None


def averages(t, espn_id):
    """Per-game row in the same shape as fetchers.espn.player_season."""
    g = t["gp"]
    pts, fga, fta = t["pts"] / g, t["fga"] / g, t["fta"] / g
    return {"espn_id": espn_id, "gp": g, "min": round(t["min"] / g, 1), "pts": round(pts, 1),
            "reb": round(t["reb"] / g, 1), "ast": round(t["ast"] / g, 1), "stl": round(t["stl"] / g, 1),
            "blk": round(t["blk"] / g, 1), "tov": round(t["tov"] / g, 1),
            "fgm": round(t["fgm"] / g, 1), "fga": round(fga, 1), "fg_pct": _pct(t["fgm"], t["fga"]),
            "fg3m": round(t["fg3m"] / g, 1), "fg3a": round(t["fg3a"] / g, 1), "fg3_pct": _pct(t["fg3m"], t["fg3a"]),
            "ftm": round(t["ftm"] / g, 1), "fta": round(fta, 1), "ft_pct": _pct(t["ftm"], t["fta"]),
            "ts_pct": round(100 * pts / (2 * (fga + 0.44 * fta)), 1) if fga + fta else None,
            "dd2": t["dd2"], "td3": t["td3"]}


def team_averages(season):
    """{team_id: per-game team box numbers and games} (team box score, else the players' sums)."""
    d = HISTORY / str(season)
    games, players = _rows(season)
    box = {(b["event_id"], b["team_id"]): b for b in _read(d / "team_box.csv.gz") if b["event_id"] in games}
    summed = defaultdict(lambda: defaultdict(float))
    for p in players:
        for k in COUNT:
            summed[(p["event_id"], p["team_id"])][k] += _f(p.get(k))
    tot = defaultdict(lambda: defaultdict(float))
    for g in games.values():
        for tid in (g["home_id"], g["away_id"]):
            src = box.get((g["event_id"], tid)) or summed.get((g["event_id"], tid))
            if not src:
                continue
            t = tot[tid]
            t["gp"] += 1
            for k in ("fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "oreb", "ast", "tov", "stl", "blk"):
                t[k] += _f(src.get(k))
    out = {}
    for tid, t in tot.items():
        g = t["gp"]
        out[tid] = {"gp": int(g), "fg_pct": _pct(t["fgm"], t["fga"]), "fg3_pct": _pct(t["fg3m"], t["fg3a"]),
                    "ft_pct": _pct(t["ftm"], t["fta"]),
                    **{k: t[k] / g for k in ("reb", "oreb", "ast", "tov", "stl", "blk", "fga", "fta")}}
    return out


def vs_opponents(espn_id, season):
    """Per-opponent averages for one season from box scores, shaped like fetchers.espn.player_vs_opponents."""
    games, players = _rows(season)
    by = defaultdict(list)
    for p in players:
        if p["athlete_id"] != espn_id:
            continue
        g = games[p["event_id"]]
        opp = g["away_abbr"] if g["home_id"] == p["team_id"] else g["home_abbr"]
        by[opp].append(p)
    out = []
    for opp, ps in by.items():
        n = len(ps)
        avg = lambda k: round(sum(_f(p[k]) for p in ps) / n, 1)
        out.append({"season": season, "opp_abbr": opp, "gp": n, "min": avg("min"), "pts": avg("pts"),
                    "reb": avg("reb"), "ast": avg("ast"), "fgm": avg("fgm"), "fga": avg("fga"),
                    "fg3m": avg("fg3m"), "fg3a": avg("fg3a"), "ftm": avg("ftm"), "fta": avg("fta")})
    return out


def refresh(con, today, season, luka_id=None, upsert=None):
    """Bring today's snapshot up to date with the box scores. Returns a short summary of what changed."""
    lines = player_lines(season)
    changed = defaultdict(int)
    # Tracked players' season averages (and, for Luka, the games ESPN's career totals are still missing)
    for r in [dict(r) for r in con.execute("SELECT * FROM player_season WHERE snapshot_date=?", (today,))]:
        t = lines.get(r["espn_id"])
        if t and t["gp"] > (r["gp"] or 0):
            if r["espn_id"] == luka_id:
                _top_up_career(con, today, r["espn_id"], t, t["gp"] - int(r["gp"] or 0))
            upsert(con, "player_season", {"snapshot_date": today, **averages(t, r["espn_id"])})
            changed["players"] += 1
    # League-wide stats used for ranks
    for r in [dict(r) for r in con.execute("SELECT * FROM league_players WHERE snapshot_date=?", (today,))]:
        t = lines.get(r["espn_id"])
        if t and t["gp"] > (r["gp"] or 0):
            a = averages(t, r["espn_id"])
            upsert(con, "league_players", {**r, **{k: a[k] for k in a if k in r}})
            changed["league"] += 1
    # Luka's per-opponent splits for this season
    if luka_id:
        espn_gp = con.execute("SELECT COALESCE(SUM(gp), 0) FROM luka_vs_opp WHERE season=?", (season,)).fetchone()[0]
        mine = vs_opponents(luka_id, season)
        if sum(r["gp"] for r in mine) > espn_gp:
            con.execute("DELETE FROM luka_vs_opp WHERE season=?", (season,))
            for r in mine:
                upsert(con, "luka_vs_opp", r)
            changed["splits"] = 1
    # Team box averages, pace and ratings (standings and points per game are already current)
    teams = team_averages(season)
    for r in [dict(r) for r in con.execute("SELECT * FROM team_season WHERE snapshot_date=?", (today,))]:
        t = teams.get(r["team_id"])
        if not t or t["gp"] != (r["wins"] or 0) + (r["losses"] or 0):
            continue                                   # a box score is missing: keep ESPN's numbers
        poss = t["fga"] - t["oreb"] + t["tov"] + 0.44 * t["fta"]
        new = {k: round(t[k], 3) if isinstance(t[k], float) else t[k]
               for k in ("fg_pct", "fg3_pct", "ft_pct", "reb", "oreb", "ast", "tov", "stl", "blk")}
        if poss and r["ppg"] is not None:
            new.update(pace=round(poss, 1), ortg=round(100 * r["ppg"] / poss, 1), drtg=round(100 * r["opp_ppg"] / poss, 1))
            new["net_rtg"] = round(new["ortg"] - new["drtg"], 1)
        if any(r.get(k) != v for k, v in new.items()):
            upsert(con, "team_season", {**r, **new})
            changed["teams"] += 1
    return dict(changed)


def _top_up_career(con, today, espn_id, t, missing):
    """Add the newest `missing` games to ESPN's career totals (they're not in ESPN's feed yet)."""
    row = con.execute("SELECT * FROM player_career WHERE snapshot_date=? AND espn_id=?", (today, espn_id)).fetchone()
    if not row or missing <= 0:
        return
    row = dict(row)
    for p in t["games"][-missing:]:
        row["gp"] = (row["gp"] or 0) + 1
        for k in ("pts", "reb", "ast", "fg3m", "stl", "blk"):
            row[k] = (row[k] or 0) + _f(p[k])
        dd, td = _doubles(p)
        row["dd2"] = (row["dd2"] or 0) + dd
        row["td3"] = (row["td3"] or 0) + td
    cols = ", ".join(f"{k}=?" for k in row if k not in ("snapshot_date", "espn_id"))
    con.execute(f"UPDATE player_career SET {cols} WHERE snapshot_date=? AND espn_id=?",
                [row[k] for k in row if k not in ("snapshot_date", "espn_id")] + [today, espn_id])
