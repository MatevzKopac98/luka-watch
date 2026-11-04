"""Five-man lineups, rotation patterns and usage for one season, from the stored stints and box scores.

- Lineups: every five-man unit's minutes and points scored/allowed per 100 possessions while it played.
  Possessions = FGA - OREB + TOV + 0.44 x FTA, from stints scaled to the official box score, averaged over
  both teams so a unit's offense and defense share one pace.
- Rotation: for each player, the share of the team's games he was on court in each stretch of regulation
  (16 stretches of 3 minutes), which shows who starts, who closes and when the bench comes in.
- Usage: share of the team's plays a player finishes (shot, free throws or turnover) while on court.
"""
from collections import defaultdict

from model.defense import HISTORY, _f, _read, calibrate, team_totals

BINS = 16                     # 3-minute stretches of a 48-minute game
BIN_SECS = 2880 / BINS


def _poss(s, side):
    return _f(s[f"{side}_fga"]) - _f(s[f"{side}_oreb"]) + _f(s[f"{side}_tov"]) + 0.44 * _f(s[f"{side}_fta"])


def load(season):
    """(stints scaled to the box score, {event_id: (home_id, away_id)}) for one season."""
    d = HISTORY / str(season)
    games = {g["event_id"]: (g["home_id"], g["away_id"], season) for g in _read(d / "games.csv.gz")}
    stints = _read(d / "stints.csv.gz")
    calibrate(stints, games, team_totals(d))
    return stints, {e: g[:2] for e, g in games.items()}


def lineups(stints, games):
    """{team_id: [unit, ...]} where unit = {ids, minutes, games, ortg, drtg, net, poss}, most minutes first."""
    acc = defaultdict(lambda: {"sec": 0.0, "pts": 0.0, "opp_pts": 0.0, "poss": 0.0, "games": set()})
    for s in stints:
        g = games.get(s["event_id"])
        if not g:
            continue
        both = (_poss(s, "home") + _poss(s, "away")) / 2
        for side, other, team in (("home", "away", g[0]), ("away", "home", g[1])):
            ids = s[f"{side}_ids"]
            if len(ids.split("|")) != 5:
                continue
            a = acc[(team, ids)]
            a["sec"] += _f(s["seconds"])
            a["pts"] += _f(s[f"{side}_pts"])
            a["opp_pts"] += _f(s[f"{other}_pts"])
            a["poss"] += both
            a["games"].add(s["event_id"])
    out = defaultdict(list)
    for (team, ids), a in acc.items():
        p = a["poss"]
        ortg = 100 * a["pts"] / p if p else None
        drtg = 100 * a["opp_pts"] / p if p else None
        out[team].append({"ids": ids.split("|"), "minutes": a["sec"] / 60, "games": len(a["games"]), "poss": p,
                          "ortg": ortg, "drtg": drtg,
                          "net": ortg - drtg if ortg is not None else None})
    for units in out.values():
        units.sort(key=lambda u: -u["minutes"])
    return dict(out)


def rotation(stints, games):
    """{team_id: {athlete_id: [share of his games on court in each 3-minute stretch]}} plus {team_id: games}.

    Shares are over the games the player appeared in, so an injured star still shows his usual pattern.
    """
    on = defaultdict(lambda: defaultdict(lambda: [0.0] * BINS))
    team_games, played = defaultdict(set), defaultdict(set)
    for s in stints:
        g = games.get(s["event_id"])
        if not g or int(s["period"]) > 4:
            continue
        start = _f(s["start_sec"])
        end = start + _f(s["seconds"])
        for side, team in (("home", g[0]), ("away", g[1])):
            team_games[team].add(s["event_id"])
            for pid in s[f"{side}_ids"].split("|"):
                if not pid:
                    continue
                row = on[team][pid]
                played[(team, pid)].add(s["event_id"])
                b = int(start // BIN_SECS)
                while b < BINS and b * BIN_SECS < end:
                    lo, hi = max(start, b * BIN_SECS), min(end, (b + 1) * BIN_SECS)
                    row[b] += max(hi - lo, 0)
                    b += 1
    n = {t: len(v) for t, v in team_games.items()}
    return ({t: {pid: [min(x / (BIN_SECS * len(played[(t, pid)])), 1.0) for x in row] for pid, row in players.items()}
             for t, players in on.items()}, n)


def usage(season):
    """{(team_id, athlete_id): {name, gp, gs, min, usg, pts_share}} from the box scores of one season."""
    d = HISTORY / str(season)
    team = team_totals(d)                                     # falls back to summed players where missing
    team_min, team_pts = defaultdict(float), defaultdict(float)
    rows = _read(d / "players.csv.gz")
    for p in rows:
        team_min[(p["event_id"], p["team_id"])] += _f(p["min"])
        team_pts[(p["event_id"], p["team_id"])] += _f(p["pts"])
    acc = {}
    for p in rows:
        m = _f(p["min"])
        key = (p["event_id"], p["team_id"])
        t = team.get(key)
        if not m or not t:
            continue
        a = acc.setdefault((p["team_id"], p["athlete_id"]), {"name": p["name"], "gp": 0, "gs": 0, "min": 0.0,
                                                             "plays": 0.0, "team_plays_on": 0.0,
                                                             "pts": 0.0, "team_pts": 0.0})
        a["gp"] += 1
        a["gs"] += p["starter"] == "1"
        a["min"] += m
        a["plays"] += _f(p["fga"]) + 0.44 * _f(p["fta"]) + _f(p["tov"])
        team_plays = _f(t["fga"]) + 0.44 * _f(t["fta"]) + _f(t["tov"])
        a["team_plays_on"] += team_plays * m / (team_min[key] / 5) if team_min[key] else 0
        a["pts"] += _f(p["pts"])
        a["team_pts"] += team_pts[key]
    return {k: {"name": a["name"], "gp": a["gp"], "gs": a["gs"], "min": a["min"] / a["gp"],
                "usg": 100 * a["plays"] / a["team_plays_on"] if a["team_plays_on"] else None,
                "pts_share": 100 * a["pts"] / a["team_pts"] if a["team_pts"] else None}
            for k, a in acc.items()}
