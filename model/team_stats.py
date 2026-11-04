"""Season stat lines for every player, from the stored box scores (history/<season>/players.csv.gz)."""
from collections import defaultdict

from model.defense import HISTORY, _f, _read

COUNTS = ("min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
          "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "plus_minus")


def season_lines(season):
    """{athlete_id: totals + gp/gs + the team he played most minutes for} for one season."""
    out = {}
    for p in _read(HISTORY / str(season) / "players.csv.gz"):
        if not _f(p["min"]):
            continue
        d = out.setdefault(p["athlete_id"], {"name": p["name"], "gp": 0, "gs": 0,
                                             "team_min": defaultdict(float), **{k: 0.0 for k in COUNTS}})
        d["gp"] += 1
        d["gs"] += p.get("starter") == "1"
        for k in COUNTS:
            d[k] += _f(p.get(k))
        d["team_min"][p["team_id"]] += _f(p["min"])
    for d in out.values():
        d["team_id"] = max(d["team_min"], key=d["team_min"].get)
        d["teams"] = sorted(d["team_min"], key=d["team_min"].get, reverse=True)
    return out


def view(line, mode):
    """Stat values for display: mode is 'game' (per game), 'p36' (per 36 minutes) or 'total'."""
    if not line or not line["gp"]:
        return None
    scale = {"game": 1 / line["gp"], "p36": 36 / line["min"] if line["min"] else 0, "total": 1}[mode]
    v = {k: line[k] * scale for k in COUNTS}
    v["min"] = line["min"] / line["gp"] if mode != "total" else line["min"]
    v["gp"], v["gs"] = line["gp"], line["gs"]
    pct = lambda m, a: 100 * line[m] / line[a] if line[a] else None
    v["fg_pct"], v["fg3_pct"], v["ft_pct"] = pct("fgm", "fga"), pct("fg3m", "fg3a"), pct("ftm", "fta")
    tsa = line["fga"] + 0.44 * line["fta"]
    v["ts_pct"] = 100 * line["pts"] / (2 * tsa) if tsa else None
    return v
