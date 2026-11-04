"""Player defensive ratings from play-by-play stints, and roster-projected team defense.

For every player across the stored seasons (recent seasons weighted more):
    on_drtg     points allowed per 100 possessions while he's on the court
    on_off      his team's defense with him on minus with him off (negative = defends better with him)
    def_impact  adjusted impact from a ridge regression over every stint: separates a player from the
                teammates and opponents he shared the floor with (negative = fewer points allowed)
    off_impact  the same for offense (positive = more points scored)

Team projection: each team's current roster, weighted by expected minutes, gives a defensive rating
built from the players' track records, wherever they played before. Early in the season this is more
informative than the team's own few games; it is blended with the real results as games accumulate.
"""
import csv
import gzip
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import sparse

DATA = Path(__file__).resolve().parent.parent / "data"
HISTORY = DATA / "history"
SEASON_WEIGHT = {0: 1.0, 1: 0.7, 2: 0.4}      # current season, last season, two seasons ago
RIDGE = 2500.0                                # shrinkage, in possessions: low-minute players pulled to 0
BLEND_GAMES = 15                              # games after which actual results count as much as roster


def _read(path):
    """All complete rows of a gzipped CSV (tolerates a file still being appended to)."""
    rows = []
    if not path.exists():
        return rows
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if None not in r and None not in r.values():
                    rows.append(r)
    except (EOFError, OSError, csv.Error):
        pass
    return rows


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def poss(s, side):
    """Possession estimate for one team in one stint."""
    return _f(s[f"{side}_fga"]) - _f(s[f"{side}_oreb"]) + _f(s[f"{side}_tov"]) + 0.44 * _f(s[f"{side}_fta"])


CALIBRATE = ("fga", "fta", "tov", "oreb")


def calibrate(stints, games, team_box):
    """Scale each team's stint counts in a game to that game's official box score.

    Play-by-play logs some events the box score doesn't count (e.g. team rebounds), which skews
    possessions; scaling per team and game makes the totals exact while keeping the split by lineup.
    """
    sums = defaultdict(float)
    for s in stints:
        for side in ("home", "away"):
            for k in CALIBRATE:
                sums[(s["event_id"], side, k)] += _f(s[f"{side}_{k}"])
    for s in stints:
        g = games.get(s["event_id"])
        if not g:
            continue
        for side, team in (("home", g[0]), ("away", g[1])):
            box = team_box.get((s["event_id"], team))
            if not box:
                continue
            for k in CALIBRATE:
                have = sums[(s["event_id"], side, k)]
                if have > 0:
                    s[f"{side}_{k}"] = _f(s[f"{side}_{k}"]) * _f(box[k]) / have


def team_totals(season_dir):
    """{(event_id, team_id): counts} from the team box score, or summed from players where it's missing."""
    totals = {(b["event_id"], b["team_id"]): b for b in _read(season_dir / "team_box.csv.gz")}
    summed = defaultdict(lambda: defaultdict(float))
    for p in _read(season_dir / "players.csv.gz"):
        key = (p["event_id"], p["team_id"])
        if key not in totals:
            for k in CALIBRATE:
                summed[key][k] += _f(p.get(k))
    totals.update(summed)
    return totals


def load_stints(current_season):
    """[(season_weight, stint)], plus {event_id: (home_id, away_id, season)}."""
    out, games = [], {}
    for season_dir in sorted(HISTORY.glob("*")):
        if not season_dir.is_dir():
            continue
        season = int(season_dir.name)
        w = SEASON_WEIGHT.get(current_season - season)
        if w is None:
            continue
        season_games = {g["event_id"]: (g["home_id"], g["away_id"], season)
                        for g in _read(season_dir / "games.csv.gz")}
        team_box = team_totals(season_dir)
        stints = _read(season_dir / "stints.csv.gz")
        calibrate(stints, season_games, team_box)
        games.update(season_games)
        out += [(w, s) for s in stints]
    return out, games


def player_info(current_season):
    """{athlete_id: {name, team_id (latest), minutes per season}} from stored box scores."""
    info = {}
    for season_dir in sorted(HISTORY.glob("*")):
        if not season_dir.is_dir():
            continue
        season = int(season_dir.name)
        games = {g["event_id"]: g["us_date"] for g in _read(season_dir / "games.csv.gz")}
        for p in _read(season_dir / "players.csv.gz"):
            d = info.setdefault(p["athlete_id"], {"name": p["name"], "team_id": None, "last_date": "",
                                                  "min": defaultdict(float), "gp": defaultdict(int)})
            date = games.get(p["event_id"], "")
            if date >= d["last_date"]:
                d["name"], d["team_id"], d["last_date"] = p["name"], p["team_id"], date
            d["min"][season] += _f(p["min"])
            d["gp"][season] += 1
    return info


def player_ratings(current_season):
    stints, games = load_stints(current_season)
    # --- raw on-court numbers and team totals for on/off -----------------------------------
    on = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])       # pid -> [opp pts, def poss, own pts, off poss] (weighted)
    team_tot = defaultdict(lambda: [0.0, 0.0])           # (team, season) -> [opp pts, poss]
    player_team = defaultdict(lambda: [0.0, 0.0])        # (pid, team, season) -> [opp pts, poss] while on
    rows, cols, vals, y, wts = [], [], [], [], []
    index = {}

    def col(pid, kind):
        key = (pid, kind)
        if key not in index:
            index[key] = len(index)
        return index[key]

    r = 0
    for w, s in stints:
        hp, ap = poss(s, "home"), poss(s, "away")
        p = (hp + ap) / 2
        if p <= 0.5:
            continue
        home_ids, away_ids = s["home_ids"].split("|"), s["away_ids"].split("|")
        if len(home_ids) != 5 or len(away_ids) != 5:
            continue
        h_team, a_team, season = games.get(s["event_id"], (None, None, None))
        for off_ids, def_ids, pts, off_team, def_team, is_home in (
                (home_ids, away_ids, _f(s["home_pts"]), h_team, a_team, 1),
                (away_ids, home_ids, _f(s["away_pts"]), a_team, h_team, 0)):
            for pid in def_ids:
                on[pid][0] += w * pts
                on[pid][1] += w * p
                player_team[(pid, def_team, season)][0] += w * pts
                player_team[(pid, def_team, season)][1] += w * p
            for pid in off_ids:
                on[pid][2] += w * pts
                on[pid][3] += w * p
            team_tot[(def_team, season)][0] += w * pts
            team_tot[(def_team, season)][1] += w * p
            # regression row: offense players +1 (offense cols), defense players +1 (defense cols)
            for pid in off_ids:
                rows.append(r); cols.append(col(pid, "o")); vals.append(1.0)
            for pid in def_ids:
                rows.append(r); cols.append(col(pid, "d")); vals.append(1.0)
            rows.append(r); cols.append(col("_home", "h")); vals.append(float(is_home))
            y.append(100.0 * pts / p)
            wts.append(w * p)
            r += 1
    y, wts = np.array(y), np.array(wts)
    league = float(np.average(y, weights=wts))
    X = sparse.csr_matrix((vals, (rows, cols)), shape=(r, len(index)))
    Xw = X.multiply(wts[:, None]).tocsr()
    A = (X.T @ Xw).toarray() + RIDGE * np.eye(len(index))
    b = Xw.T @ (y - league)
    beta = np.linalg.solve(A, b)

    # --- on/off: team defense with the player off the court ---------------------------------
    on_off = {}
    for (pid, team, season), (pts_on, p_on) in player_team.items():
        tp, tposs = team_tot[(team, season)]
        p_off = tposs - p_on
        if p_on < 100 or p_off < 100:
            continue
        diff = 100 * pts_on / p_on - 100 * (tp - pts_on) / p_off
        acc = on_off.setdefault(pid, [0.0, 0.0])
        acc[0] += diff * p_on
        acc[1] += p_on

    ratings = {}
    for (pid, kind), i in index.items():
        if kind == "h":
            continue
        d = ratings.setdefault(pid, {"athlete_id": pid})
        d["def_impact" if kind == "d" else "off_impact"] = round(float(beta[i]), 2)
    for pid, d in ratings.items():
        o = on[pid]
        d["def_poss"] = round(o[1])
        d["on_drtg"] = round(100 * o[0] / o[1], 1) if o[1] else None
        d["on_ortg"] = round(100 * o[2] / o[3], 1) if o[3] else None
        d["on_off"] = round(on_off[pid][0] / on_off[pid][1], 1) if pid in on_off else None
    return ratings, league


def team_ratings_by_season(season):
    """{team_id: (offensive rating, defensive rating)} for one stored season, from its stints (unweighted)."""
    games = {g["event_id"]: (g["home_id"], g["away_id"]) for g in _read(HISTORY / str(season) / "games.csv.gz")}
    team_box = team_totals(HISTORY / str(season))
    stints = _read(HISTORY / str(season) / "stints.csv.gz")
    calibrate(stints, games, team_box)
    scored, allowed, possessions = defaultdict(float), defaultdict(float), defaultdict(float)
    for s in stints:
        h, a = games.get(s["event_id"], (None, None))
        p = (poss(s, "home") + poss(s, "away")) / 2
        scored[h] += _f(s["home_pts"]); allowed[h] += _f(s["away_pts"])
        scored[a] += _f(s["away_pts"]); allowed[a] += _f(s["home_pts"])
        possessions[h] += p
        possessions[a] += p
    return {t: (round(100 * scored[t] / p, 1), round(100 * allowed[t] / p, 1))
            for t, p in possessions.items() if t and p}


def team_projection(ratings, league, rosters, minutes, injured, actual):
    """Projected offensive and defensive rating per team from its current roster.

    rosters: {team_id: [athlete_id]}, minutes: {athlete_id: expected minutes}, injured: set of ids out,
    actual: {team_id: (ortg this season, drtg this season, games played)}
    """
    out = {}
    for team, ids in rosters.items():
        avail = [(pid, minutes.get(pid, 8.0)) for pid in ids if pid not in injured]
        total = sum(m for _, m in avail) or 1.0
        share = lambda m: m * (240.0 / total) / 48.0        # minutes scaled to 5 x 48, as share of a game
        o_proj = league + sum(share(m) * ratings.get(pid, {}).get("off_impact", 0.0) for pid, m in avail)
        d_proj = league + sum(share(m) * ratings.get(pid, {}).get("def_impact", 0.0) for pid, m in avail)
        ortg, drtg, gp = actual.get(team, (None, None, 0))
        w = gp / (gp + BLEND_GAMES) if gp else 0.0
        blend = lambda real, proj: round(w * real + (1 - w) * proj, 1) if real else round(proj, 1)
        out[team] = {"roster_ortg": round(o_proj, 1), "roster_drtg": round(d_proj, 1),
                     "actual_ortg": ortg, "actual_drtg": drtg, "gp": gp,
                     "blend_ortg": blend(ortg, o_proj), "blend_drtg": blend(drtg, d_proj)}
    return out
