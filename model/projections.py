"""Player and game projections for one night.

Player projection = expected minutes x production per minute, adjusted for the matchup:
  minutes     recent games; + share of injured teammates' minutes; - on the 2nd night of a back-to-back;
              - for starters when a big spread signals a likely blowout
  per minute  recent games weighted most, then the rest of this season, then last season;
              + usage when high-volume teammates are out
  matchup     opponent's roster-projected defence (injured defenders drop out) and the game's pace
The spread of outcomes comes from the player's own game-to-game variation (per-minute output and minutes),
which turns a projection into a probability of going over or under a line.
"""
import math
from collections import defaultdict
from statistics import mean, pstdev

from scipy.stats import gamma

from model.defense import HISTORY, _f, _read

BASE = ("pts", "reb", "ast", "fg3m")
COMBOS = {"pts+reb": ("pts", "reb"), "pts+ast": ("pts", "ast"), "reb+ast": ("reb", "ast"),
          "pts+reb+ast": ("pts", "reb", "ast")}
USAGE_K = {"pts": 0.35, "ast": 0.30, "reb": 0.20, "fg3m": 0.35}   # rate boost per unit of missing team share
DEFENCE_SENSITIVE = {"pts", "fg3m", "ast"}
RECENT_GAMES = 10
MARGIN_SD, TOTAL_SD = 12.5, 17.5                                # NBA game-to-game spread around projections
HOME_EDGE = 1.2                                                 # points per team


def load_logs():
    """{athlete_id: [game rows oldest first]} across every stored season, with date/opponent/season."""
    logs = defaultdict(list)
    for season_dir in sorted(p for p in HISTORY.glob("*") if p.is_dir()):
        season = int(season_dir.name)
        games = {g["event_id"]: g for g in _read(season_dir / "games.csv.gz")}
        for p in _read(season_dir / "players.csv.gz"):
            g = games.get(p["event_id"])
            if not g or not _f(p["min"]):
                continue
            home = p["team_id"] == g["home_id"]
            logs[p["athlete_id"]].append({
                "event_id": p["event_id"], "date": g["us_date"], "season": season, "team_id": p["team_id"],
                "opp_id": g["away_id"] if home else g["home_id"], "name": p["name"],
                "starter": p.get("starter") == "1", **{k: _f(p[k]) for k in ("min", *BASE)},
            })
    for v in logs.values():
        v.sort(key=lambda g: g["date"])
    return logs


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def prob_over(line, mu, sd):
    """P(stat > line) from a gamma distribution with this mean and spread.

    Player stats are right-skewed (usual nights plus the odd big one), so the typical game sits below
    the average; a symmetric curve would make every over look too likely. Lines are x.5, so no push.
    """
    mu, sd = max(mu, 0.05), max(sd, 0.3)
    shape, scale = (mu / sd) ** 2, sd ** 2 / mu
    return float(gamma.sf(line, shape, scale=scale))


def _stat(g, stat):
    return sum(g[s] for s in COMBOS[stat]) if stat in COMBOS else g[stat]


def baseline(games, season):
    """Minutes and per-minute rates before matchup adjustments. games: played games before tonight."""
    cur = [g for g in games if g["season"] == season]
    last = [g for g in games if g["season"] == season - 1]
    if len(cur) + len(last) < 3:
        return None
    pool = cur[-5:] if len(cur) >= 3 else last[-15:] or cur
    minutes = mean(g["min"] for g in pool)
    parts = [(cur[-RECENT_GAMES:], 3.0), (cur, 1.0), (last, 0.6)]
    rates = {}
    for stat in BASE:
        num = sum(w * sum(g[stat] for g in gs) for gs, w in parts)
        den = sum(w * sum(g["min"] for g in gs) for gs, w in parts)
        rates[stat] = num / den if den else 0.0
    sample = (cur + last)[-25:] if len(cur) < 25 else cur[-25:]
    return {"minutes": minutes, "rates": rates, "sample": sample, "cur": cur,
            "min_sd": pstdev([g["min"] for g in sample]) if len(sample) > 2 else 4.0}


def project(games, season, ctx):
    """Full projection for one player tonight. ctx keys: extra_minutes, usage_share {stat: share},
    blowout (abs spread), b2b, starter, opp_factor, pace_factor."""
    b = baseline(games, season)
    if not b:
        return None
    notes = []
    m = b["minutes"]
    if ctx.get("extra_minutes"):
        m += ctx["extra_minutes"]
        notes.append(f"+{ctx['extra_minutes']:.1f} min with teammates out")
    starter = m >= 24
    if ctx.get("b2b") and starter:
        m *= 0.97
        notes.append("2nd night of a back-to-back")
    spread = ctx.get("blowout", 0)
    if spread >= 9 and starter:
        cut = min(0.01 * (spread - 7), 0.08)
        m *= 1 - cut
        notes.append(f"blowout risk (spread {spread:.1f}): -{cut * 100:.0f}% minutes")
    m = min(m, 40.0)
    rates = dict(b["rates"])
    for stat, share in (ctx.get("usage_share") or {}).items():
        if share > 0.03:
            rates[stat] *= 1 + USAGE_K.get(stat, 0) * share
    for stat in DEFENCE_SENSITIVE:
        rates[stat] *= ctx.get("opp_factor", 1.0)
    for stat in BASE:
        rates[stat] *= ctx.get("pace_factor", 1.0)

    out = {"minutes": round(m, 1), "base_minutes": round(b["minutes"], 1), "notes": notes, "stats": {}}
    for stat in (*BASE, *COMBOS):
        rate = sum(rates[s] for s in COMBOS[stat]) if stat in COMBOS else rates[stat]
        mu = m * rate
        per_min = [_stat(g, stat) / g["min"] for g in b["sample"] if g["min"] >= 8]
        rate_sd = pstdev(per_min) if len(per_min) > 2 else rate * 0.4
        sd = math.sqrt((m * rate_sd) ** 2 + (rate * b["min_sd"]) ** 2)
        sd = max(sd, 0.9 * math.sqrt(max(mu, 0.5)))              # counting stats can't be tighter than ~Poisson
        last5 = [_stat(g, stat) for g in b["cur"][-5:]]
        out["stats"][stat] = {"mean": round(mu, 2), "sd": round(sd, 2),
                              "last5": round(mean(last5), 1) if last5 else None}
    return out


def game(team, opp, home, league, pace_t, pace_o, league_pace):
    """Projected points for both sides from blended ratings and pace. team/opp: team_projection rows."""
    pace = (pace_t + pace_o) / 2 if pace_t and pace_o else league_pace
    pts_t = pace * team["blend_ortg"] * opp["blend_drtg"] / league / 100 + (HOME_EDGE if home else -HOME_EDGE)
    pts_o = pace * opp["blend_ortg"] * team["blend_drtg"] / league / 100 + (-HOME_EDGE if home else HOME_EDGE)
    return {"pace": round(pace, 1), "team_pts": round(pts_t, 1), "opp_pts": round(pts_o, 1),
            "margin": round(pts_t - pts_o, 1), "total": round(pts_t + pts_o, 1)}
