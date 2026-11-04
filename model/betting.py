"""Betting engine: league-wide projections, Lakers picks, grading and learning.

The betting database (data/betting.sqlite) is private: it is stored encrypted on the data branch and
only decrypted inside a run that has the password (see model/crypto.py).

Flow of one run:
  grade()      settle finished picks (result, closing line, why a miss happened) and fill in actual results
               for every league-wide projection; then recalibrate()
  predict()    project every player with a prop in tonight's games (all teams = training data) and
               choose Lakers picks where our probability beats the market's fair probability
Picks are refreshed while lines move; the version that counts is the last one before tip-off. A pick
that loses its edge before the game is marked withdrawn and never counted.
"""
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from model import projections as pj
from model import props as pr
from model.defense import HISTORY, _f, _read

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "betting.sqlite"
LAKERS_ID = "13"
MIN_EDGE = 0.05            # our probability must beat the market's fair probability by 5 points
MIN_EV = 0.03              # and the price must give +3% expected value
MAX_PICKS = 6
PLAY_MARGIN = 0.03         # "still worth it" while our probability beats the price's break-even by 3 points
DEFAULT_MODEL_WEIGHT = 0.5 # before enough results exist: half our model, half the market
MIN_CALIBRATION_N = 400
RECENT_ABSENCE_GAMES = 3   # an injured player only shifts minutes/usage if he played in the team's last 3 games

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (       -- every projected prop, every team (training data)
    us_date TEXT, event_id TEXT, athlete_id TEXT, team_id TEXT, stat TEXT, line REAL,
    mean REAL, sd REAL, minutes REAL, p_model REAL, fair_over REAL, p_final REAL, captured_at TEXT,
    actual REAL, actual_min REAL,
    PRIMARY KEY (us_date, event_id, athlete_id, stat)
);
CREATE TABLE IF NOT EXISTS picks (
    pick_id TEXT PRIMARY KEY, us_date TEXT, event_id TEXT, tip_utc TEXT, game TEXT, kind TEXT,
    athlete_id TEXT, player TEXT, stat TEXT, side TEXT, line REAL, odds TEXT, dec REAL,
    p_model REAL, p_final REAL, fair REAL, edge REAL, ev REAL, units REAL, playable_to REAL,
    reasons TEXT, projection TEXT, first_published TEXT, last_seen TEXT, status TEXT,
    close_line REAL, close_odds TEXT, clv REAL, actual REAL, result TEXT, profit REAL,
    miss_category TEXT, miss_text TEXT, graded_at TEXT
);
CREATE TABLE IF NOT EXISTS learning_log (
    logged_at TEXT, kind TEXT, text TEXT, metrics TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT
);
"""


def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="minutes")


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def get_setting(con, key, default=None):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(con, key, value):
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))


def final_probability(con, p_model, fair):
    """Combine our model with the market: the learned calibration once there's enough data."""
    cal = get_setting(con, "calibration")
    if cal:
        z = cal["a"] + cal["b"] * logit(p_model) + cal["c"] * logit(fair)
        return 1 / (1 + math.exp(-z))
    return DEFAULT_MODEL_WEIGHT * p_model + (1 - DEFAULT_MODEL_WEIGHT) * fair


# ---------------------------------------------------------------- context for one night

def team_context(team_id, opp_id, season, us_date, logs, injured, proj, pace, league, league_pace, spread_abs):
    """Per-player adjustments for one team tonight: minutes from absent teammates, usage, matchup."""
    day_before = (datetime.strptime(us_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    roster = {}
    for pid, games in logs.items():
        before = [g for g in games if g["date"] < us_date]
        if before and before[-1]["team_id"] == team_id and before[-1]["season"] >= season - 1:
            b = pj.baseline(before, season)
            if b:
                roster[pid] = (before, b)
    # Only a *recent* absence frees up minutes and shots: someone out for weeks is already missing from his
    # teammates' recent numbers, so counting him again would inflate them.
    team_dates = sorted({g["date"] for games, _ in roster.values() for g in games
                         if g["team_id"] == team_id and g["season"] == season})
    recent_dates = set(team_dates[-RECENT_ABSENCE_GAMES:])
    out = {pid for pid in roster if pid in injured and roster[pid][0][-1]["date"] in recent_dates}
    b2b = any(g["date"] == day_before for pid, (games, _) in roster.items() for g in games[-2:])
    avail = {pid: b["minutes"] for pid, (_, b) in roster.items() if pid not in out and b["minutes"] >= 12}
    out_min = sum(roster[pid][1]["minutes"] for pid in out if roster[pid][1]["minutes"] >= 10)
    team_prod = {s: sum(b["minutes"] * b["rates"][s] for _, b in roster.values()) or 1 for s in pj.BASE}
    out_prod = {s: sum(roster[pid][1]["minutes"] * roster[pid][1]["rates"][s] for pid in out) for s in pj.BASE}
    usage = {s: out_prod[s] / team_prod[s] for s in pj.BASE}
    t, o = proj.get(team_id), proj.get(opp_id)
    opp_factor = (o["blend_drtg"] / league) if o else 1.0
    game_pace = ((pace.get(team_id) or league_pace) + (pace.get(opp_id) or league_pace)) / 2
    ctx = {}
    total_avail = sum(avail.values()) or 1
    for pid in roster:
        if pid in out:
            continue
        extra = min(out_min * avail.get(pid, 0) / total_avail, 8.0) if pid in avail else 0.0
        ctx[pid] = {"extra_minutes": extra, "usage_share": usage, "b2b": b2b, "blowout": spread_abs,
                    "opp_factor": opp_factor, "pace_factor": game_pace / league_pace}
    absent = sorted(((roster[pid][1]["minutes"], roster[pid][0][-1]["name"]) for pid in out), reverse=True)
    return {"roster": roster, "ctx": ctx, "out": out, "absent": absent, "b2b": b2b,
            "opp_factor": opp_factor, "game_pace": game_pace}


# ---------------------------------------------------------------- predict

def predict(con, us_date, season, nba_con, injured, schedule, captured_label):
    """Project all props for not-yet-started games on us_date; refresh Lakers picks.

    injured: {athlete_id: status} for players out tonight; schedule: {event_id: {tip_utc, home_id, away_id,
    started}}; nba_con: site database (team ratings, pace, player defence).
    """
    files = pr.snapshots(us_date)
    if not files:
        return {"predictions": 0, "picks": 0, "note": "no props captured yet"}
    rows = pr.read_snapshot(files[-1])
    markets = pr.markets(rows)
    first_markets = pr.markets(pr.read_snapshot(files[0]))
    lines = pr.game_lines(us_date)
    logs = pj.load_logs()
    league_row = nba_con.execute("SELECT MAX(snapshot_date) d FROM team_projection").fetchone()
    proj = {r["team_id"]: dict(r) for r in nba_con.execute(
        "SELECT * FROM team_projection WHERE snapshot_date=?", (league_row["d"],))}
    league = next(iter(proj.values()))["league_rtg"] if proj else 112.0
    pace = {r["team_id"]: r["pace"] for r in nba_con.execute(
        "SELECT team_id, pace FROM team_season WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM team_season)")}
    league_pace = float(np.mean([v for v in pace.values() if v])) if pace else 100.0
    names = {r["athlete_id"]: r["name"] for r in nba_con.execute("SELECT athlete_id, name FROM player_defense")}
    defence = {r["athlete_id"]: r["def_impact"] for r in nba_con.execute("SELECT athlete_id, def_impact FROM player_defense")}

    n_pred, lakers_candidates, contexts = 0, [], {}
    for event_id, info in schedule.items():
        if info["started"] or event_id not in lines:
            continue
        gl = lines[event_id]
        spread_home = _f(gl["spread"]) or 0.0
        for team_id, opp_id in ((info["home_id"], info["away_id"]), (info["away_id"], info["home_id"])):
            contexts[(event_id, team_id)] = team_context(team_id, opp_id, season, us_date, logs, injured, proj,
                                                         pace, league, league_pace, abs(spread_home))
        for (ev, athlete, stat), mk in markets.items():
            if ev != event_id:
                continue
            team_id = next((t for t in (info["home_id"], info["away_id"])
                            if athlete in contexts[(event_id, t)]["ctx"]), None)
            if not team_id:
                continue
            tc = contexts[(event_id, team_id)]
            games, _ = tc["roster"][athlete]
            p = pj.project(games, season, tc["ctx"][athlete])
            if not p:
                continue
            s = p["stats"][stat]
            p_model = pj.prob_over(mk["line"], s["mean"], s["sd"])
            p_final = final_probability(con, p_model, mk["fair_over"])
            con.execute("""INSERT OR REPLACE INTO predictions (us_date, event_id, athlete_id, team_id, stat, line,
                           mean, sd, minutes, p_model, fair_over, p_final, captured_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (us_date, event_id, athlete, team_id, stat, mk["line"], s["mean"], s["sd"], p["minutes"],
                         p_model, mk["fair_over"], p_final, mk["captured_at"]))
            n_pred += 1
            if team_id == LAKERS_ID:
                opp_id = info["away_id"] if team_id == info["home_id"] else info["home_id"]
                for side in ("over", "under"):
                    pf = p_final if side == "over" else 1 - p_final
                    fair = mk["fair_over"] if side == "over" else 1 - mk["fair_over"]
                    dec = mk[f"{side}_dec"]
                    lakers_candidates.append({
                        "kind": "prop", "event_id": event_id, "athlete_id": athlete, "stat": stat, "side": side,
                        "line": mk["line"], "line_open": first_markets.get((ev, athlete, stat), {}).get("line"),
                        "odds": mk[f"{side}_odds"], "dec": dec, "p_model": p_model if side == "over" else 1 - p_model,
                        "p_final": pf, "fair": fair, "edge": pf - fair, "ev": pf * dec - 1, "proj": p, "s": s,
                        "player": names.get(athlete) or games[-1]["name"], "team_id": team_id, "opp_id": opp_id,
                        "tc": tc, "opp_tc": contexts[(event_id, opp_id)], "games": games,
                    })
        if LAKERS_ID in (info["home_id"], info["away_id"]):
            lakers_candidates += game_candidates(con, event_id, info, gl, proj, pace, league, league_pace)

    picks = choose(lakers_candidates)
    published = set()
    for c in picks:
        pid = refresh_pick(con, c, us_date, schedule[c["event_id"]], proj, defence, names, season)
        published.add(pid)
    for r in con.execute("SELECT pick_id, event_id FROM picks WHERE status='active' AND us_date=?", (us_date,)):
        if r["pick_id"] not in published and not schedule.get(r["event_id"], {}).get("started"):
            con.execute("UPDATE picks SET status='withdrawn', last_seen=? WHERE pick_id=?", (now_iso(), r["pick_id"]))
    con.commit()
    return {"predictions": n_pred, "picks": len(picks), "candidates": len(lakers_candidates)}


def game_candidates(con, event_id, info, gl, proj, pace, league, league_pace):
    """Spread and total for the Lakers game from blended team ratings and pace."""
    home = info["home_id"] == LAKERS_ID
    opp_id = info["away_id"] if home else info["home_id"]
    t, o = proj.get(LAKERS_ID), proj.get(opp_id)
    if not t or not o:
        return []
    g = pj.game(t, o, home, league, pace.get(LAKERS_ID), pace.get(opp_id), league_pace)
    out = []
    spread_home = _f(gl["spread"])
    if spread_home is not None:
        lal_line = spread_home if home else -spread_home           # e.g. -11.5 = Lakers favoured by 11.5
        p_cover = 1 - pj.phi((-lal_line - g["margin"]) / pj.MARGIN_SD)
        for side, p_model, line in (("lakers", p_cover, lal_line), ("opponent", 1 - p_cover, -lal_line)):
            pf = final_probability(con, p_model, 0.5)
            out.append({"kind": "spread", "event_id": event_id, "side": side, "line": line, "odds": "-110",
                        "dec": 1.909, "p_model": p_model, "p_final": pf, "fair": 0.5, "edge": pf - 0.5,
                        "ev": pf * 1.909 - 1, "game_proj": g, "opp_id": opp_id, "stat": "spread",
                        "line_open": _f(gl["first"]["spread"])})
    total = _f(gl["total"])
    if total is not None:
        p_over = 1 - pj.phi((total - g["total"]) / pj.TOTAL_SD)
        d_over = pr.american_to_decimal(gl["over_odds"]) or 1.909
        d_under = pr.american_to_decimal(gl["under_odds"]) or 1.909
        fair_over = (1 / d_over) / (1 / d_over + 1 / d_under)
        for side, p_model, fair, dec, odds in (("over", p_over, fair_over, d_over, gl["over_odds"]),
                                               ("under", 1 - p_over, 1 - fair_over, d_under, gl["under_odds"])):
            pf = final_probability(con, p_model, fair)
            out.append({"kind": "total", "event_id": event_id, "side": side, "line": total, "odds": odds,
                        "dec": dec, "p_model": p_model, "p_final": pf, "fair": fair, "edge": pf - fair,
                        "ev": pf * dec - 1, "game_proj": g, "opp_id": opp_id, "stat": "total",
                        "line_open": _f(gl["first"]["total"])})
    return out


def choose(candidates):
    """Best picks by expected value; at most one side per market and one market per player-stat."""
    good = [c for c in candidates if c["edge"] >= MIN_EDGE and c["ev"] >= MIN_EV]
    good.sort(key=lambda c: -c["ev"])
    seen, out = set(), []
    for c in good:
        key = (c["kind"], c.get("athlete_id"), c["stat"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= MAX_PICKS:
            break
    return out


def units_for(c):
    """Quarter-Kelly stake in units (1 unit = 1% of bankroll), between 0.5 and 2."""
    kelly = (c["p_final"] * c["dec"] - 1) / (c["dec"] - 1)
    return max(0.5, min(2.0, round(kelly * 25 * 2) / 2))


def _side_prob(c, line):
    """Our model's probability that this pick wins if the line were `line` (same side)."""
    if c["kind"] == "prop":
        p_over = pj.prob_over(line, c["s"]["mean"], c["s"]["sd"])
        return p_over if c["side"] == "over" else 1 - p_over
    g = c["game_proj"]
    if c["kind"] == "spread":                                # line is from the picked side: -6.5 = giving 6.5
        margin = g["margin"] if c["side"] == "lakers" else -g["margin"]
        return 1 - pj.phi((-line - margin) / pj.MARGIN_SD)
    p_over = 1 - pj.phi((line - g["total"]) / pj.TOTAL_SD)
    return p_over if c["side"] == "over" else 1 - p_over


def playable_to(c):
    """Worst line at which the pick still beats the price's break-even by PLAY_MARGIN.

    A worse line means: higher for an over, lower for an under, fewer points for a spread side.
    """
    step = 1.0 if c["kind"] == "prop" else 0.5
    worse = step if c["side"] == "over" else -step
    line = c["line"]
    for _ in range(12):
        nxt = line + worse
        p = DEFAULT_MODEL_WEIGHT * _side_prob(c, nxt) + (1 - DEFAULT_MODEL_WEIGHT) * c["fair"]
        if p < 1 / c["dec"] + PLAY_MARGIN:
            break
        line = nxt
    return line


# ---------------------------------------------------------------- reasons (plain words, real numbers)

def reasons_for(c, proj, defence, names, season):
    r = []
    if c["kind"] == "prop":
        s, p = c["s"], c["proj"]
        label = pr.STAT_LABEL[c["stat"]]
        r.append(f"Projection {s['mean']:.1f} {label} in ~{p['minutes']:.0f} minutes vs the line {c['line']}"
                 f" ({'above' if s['mean'] > c['line'] else 'below'} by {abs(s['mean'] - c['line']):.1f}).")
        if s.get("last5") is not None:
            r.append(f"Last 5 games: {s['last5']:.1f} {label} on average.")
        tc, otc = c["tc"], c["opp_tc"]
        if tc["absent"]:
            top = ", ".join(f"{n} ({m:.0f} min)" for m, n in tc["absent"][:3])
            r.append(f"Lakers without {top}: more minutes and shots to go around.")
        opp_out = [(defence.get(pid, 0), otc["roster"][pid][0][-1]["name"], otc["roster"][pid][1]["minutes"])
                   for pid in otc["out"]]
        opp_out = [x for x in opp_out if x[0] < -0.5 and x[2] >= 15]
        if opp_out:
            d, n, m = min(opp_out)
            r.append(f"Opponent missing {n}, a strong defender (impact {d:+.1f} per 100, {m:.0f} min a game).")
        o = proj.get(c["opp_id"])
        if o and c["stat"] in ("pts", "fg3m", "pts+ast", "pts+reb", "pts+reb+ast", "ast"):
            r.append(f"Opponent defence: {o['blend_drtg']:.1f} points allowed per 100 "
                     f"(league {o['league_rtg']:.1f}; roster-projected {o['roster_drtg']:.1f}).")
        if abs(tc["game_pace"] - 100) >= 1.5:
            r.append(f"Expected pace {tc['game_pace']:.1f} possessions ({'fast' if tc['game_pace'] > 100 else 'slow'} game).")
        for n in p["notes"]:
            r.append(n[0].upper() + n[1:] + ".")
        vs = [g[c["stat"]] if c["stat"] in pj.BASE else sum(g[x] for x in pj.COMBOS[c["stat"]])
              for g in c["games"] if g["opp_id"] == c["opp_id"] and g["season"] == season - 1]
        if vs:
            r.append(f"Vs this opponent last season: {sum(vs) / len(vs):.1f} {label} ({len(vs)} games).")
    else:
        g = c["game_proj"]
        r.append(f"Projected score: Lakers {g['team_pts']:.0f}, opponent {g['opp_pts']:.0f} "
                 f"(margin {g['margin']:+.1f}, total {g['total']:.0f}, pace {g['pace']:.0f}).")
        o, t = proj.get(c["opp_id"]), proj.get(LAKERS_ID)
        if o and t:
            r.append(f"Ratings (offence/defence, roster + results blend): Lakers {t['blend_ortg']:.1f}/{t['blend_drtg']:.1f}, "
                     f"opponent {o['blend_ortg']:.1f}/{o['blend_drtg']:.1f}.")
    if c.get("line_open") is not None and c["line_open"] != c["line"]:
        r.append(f"Line moved {c['line_open']} → {c['line']} since it opened.")
    r.append(f"Our probability {c['p_final'] * 100:.0f}% vs the market's {c['fair'] * 100:.0f}% "
             f"(edge {c['edge'] * 100:+.1f} points, expected value {c['ev'] * 100:+.1f}%).")
    return r


def describe(c, names):
    if c["kind"] == "prop":
        return f"{c['player']} {c['side'].capitalize()} {c['line']} {pr.STAT_LABEL[c['stat']]}"
    if c["kind"] == "spread":
        who = "Lakers" if c["side"] == "lakers" else "Opponent"
        return f"{who} {c['line']:+.1f}"
    return f"Game total {c['side'].capitalize()} {c['line']}"


def refresh_pick(con, c, us_date, info, proj, defence, names, season):
    pick_id = f"{c['event_id']}|{c['kind']}|{c.get('athlete_id') or ''}|{c['stat']}|{c['side']}"
    exists = con.execute("SELECT first_published FROM picks WHERE pick_id=?", (pick_id,)).fetchone()
    projection = ({"minutes": c["proj"]["minutes"], "mean": c["s"]["mean"], "sd": c["s"]["sd"]}
                  if c["kind"] == "prop" else c["game_proj"])
    con.execute("""INSERT OR REPLACE INTO picks (pick_id, us_date, event_id, tip_utc, game, kind, athlete_id, player,
                   stat, side, line, odds, dec, p_model, p_final, fair, edge, ev, units, playable_to, reasons,
                   projection, first_published, last_seen, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')""",
                (pick_id, us_date, c["event_id"], info["tip_utc"], info["label"], c["kind"], c.get("athlete_id"),
                 describe(c, names), c["stat"], c["side"], c["line"], str(c["odds"]), c["dec"], c["p_model"],
                 c["p_final"], c["fair"], c["edge"], c["ev"], units_for(c), playable_to(c),
                 json.dumps(reasons_for(c, proj, defence, names, season)), json.dumps(projection),
                 exists["first_published"] if exists else now_iso(), now_iso()))
    return pick_id


# ---------------------------------------------------------------- grading

def _season_box(season):
    players = {(p["event_id"], p["athlete_id"]): p for p in _read(HISTORY / str(season) / "players.csv.gz")}
    games = {g["event_id"]: g for g in _read(HISTORY / str(season) / "games.csv.gz")}
    return players, games


def _value(row, stat):
    return sum(_f(row[s]) for s in pj.COMBOS[stat]) if stat in pj.COMBOS else _f(row[stat])


def _when(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def closing(us_date, c, tip_utc):
    """Last captured line, odds and fair probability for this pick before tip-off."""
    tip, best = _when(tip_utc), None
    for path in pr.snapshots(us_date):
        rows = pr.read_snapshot(path)
        if not rows or (tip and _when(rows[0]["captured_at"]) > tip):
            continue
        mk = pr.markets(rows).get((c["event_id"], c["athlete_id"], c["stat"]))
        if mk:
            best = (mk["line"], mk[f"{c['side']}_odds"], mk["fair_over"] if c["side"] == "over" else 1 - mk["fair_over"])
    return best


def grade(con, season):
    """Settle finished picks and fill actual results into league-wide predictions."""
    players, games = _season_box(season)
    graded = 0
    for r in con.execute("SELECT * FROM predictions WHERE actual IS NULL"):
        g = games.get(r["event_id"])
        if not g:
            continue
        p = players.get((r["event_id"], r["athlete_id"]))
        actual, mins = (_value(p, r["stat"]), _f(p["min"])) if p else (None, 0.0)
        con.execute("UPDATE predictions SET actual=?, actual_min=? WHERE us_date=? AND event_id=? AND athlete_id=? AND stat=?",
                    (actual if p else -1, mins, r["us_date"], r["event_id"], r["athlete_id"], r["stat"]))
    for c in [dict(r) for r in con.execute("SELECT * FROM picks WHERE status='active'")]:
        g = games.get(c["event_id"])
        if not g:
            continue
        home_lal = g["home_id"] == LAKERS_ID
        lal_pts = _f(g["home_score"] if home_lal else g["away_score"])
        opp_pts = _f(g["away_score"] if home_lal else g["home_score"])
        proj = json.loads(c["projection"] or "{}")
        if c["kind"] == "prop":
            p = players.get((c["event_id"], c["athlete_id"]))
            if not p:
                con.execute("UPDATE picks SET status='void', result='V', profit=0, miss_text=?, graded_at=? WHERE pick_id=?",
                            ("Did not play: bet voided (no action).", now_iso(), c["pick_id"]))
                continue
            actual = _value(p, c["stat"])
            diff = actual - c["line"]
            won = diff > 0 if c["side"] == "over" else diff < 0
            close = closing(c["us_date"], c, c["tip_utc"])
        elif c["kind"] == "spread":
            margin = lal_pts - opp_pts
            actual = margin
            cover = margin + c["line"] if c["side"] == "lakers" else -margin + c["line"]
            diff, won = cover, cover > 0
            close = None
        else:
            actual = lal_pts + opp_pts
            diff = actual - c["line"]
            won = diff > 0 if c["side"] == "over" else diff < 0
            close = None
        push = diff == 0
        result = "P" if push else ("W" if won else "L")
        profit = 0 if push else (c["units"] * (c["dec"] - 1) if won else -c["units"])
        category, text = explain(c, actual, proj, players.get((c["event_id"], c["athlete_id"])) if c["kind"] == "prop" else None,
                                 lal_pts - opp_pts, won, push)
        clv = None
        if close:
            close_line, close_odds, close_fair = close
            clv = close_fair - c["fair"]                     # positive: market moved toward our side after the pick
        con.execute("""UPDATE picks SET status='graded', actual=?, result=?, profit=?, miss_category=?, miss_text=?,
                       close_line=?, close_odds=?, clv=?, graded_at=? WHERE pick_id=?""",
                    (actual, result, profit, category, text, close[0] if close else None,
                     close[1] if close else None, clv, now_iso(), c["pick_id"]))
        graded += 1
    con.commit()
    return graded


def explain(c, actual, proj, box, margin, won, push):
    """Why the pick won or lost, in one line and one category."""
    if push:
        return "push", f"Landed exactly on {c['line']}: stake returned."
    miss_by = abs(actual - c["line"])
    if c["kind"] != "prop":
        what = "margin" if c["kind"] == "spread" else "total"
        exp = proj.get("margin" if c["kind"] == "spread" else "total")
        base = f"Final {what} {actual:+.0f}" if c["kind"] == "spread" else f"Final total {actual:.0f}"
        if won:
            return "hit", f"{base} (we projected {exp:+.1f})." if c["kind"] == "spread" else f"{base} (we projected {exp:.0f})."
        return ("close" if miss_by <= 3 else "model"), f"{base} vs our projection {exp}; missed by {miss_by:.1f}."
    mins = _f(box["min"]) if box else 0.0
    exp_min, exp_mean = proj.get("minutes", 0), proj.get("mean", 0)
    if won:
        return "hit", f"{actual:.0f} vs line {c['line']} ({'over' if actual > c['line'] else 'under'} by {miss_by:.1f}) in {mins:.0f} minutes."
    if mins < exp_min - 6:
        why = "blowout, starters sat" if abs(margin) >= 18 else "foul trouble, injury or rotation change"
        return "minutes", f"Played {mins:.0f} min instead of ~{exp_min:.0f} ({why}); finished {actual:.0f} vs line {c['line']}."
    rate_exp = exp_mean / exp_min if exp_min else 0
    rate_act = actual / mins if mins else 0
    if miss_by <= max(1.0, 0.35 * proj.get("sd", 3.0)):          # within about a third of his usual swing
        return "close", f"Close call: {actual:.0f} vs line {c['line']}, missed by {miss_by:.1f} in normal minutes ({mins:.0f})."
    if rate_exp and abs(rate_act - rate_exp) / rate_exp > 0.35:
        shot = f" Shot {_f(box['fgm']):.0f}/{_f(box['fga']):.0f}." if box and c["stat"].startswith("pts") else ""
        return "efficiency", (f"Normal minutes ({mins:.0f}) but an off night: {actual:.0f} vs projected {exp_mean:.1f}.{shot}"
                              if rate_act < rate_exp else
                              f"Normal minutes ({mins:.0f}) and a big night: {actual:.0f} vs projected {exp_mean:.1f}.")
    return "model", f"Projection {exp_mean:.1f} was off: {actual:.0f} in {mins:.0f} minutes vs line {c['line']}."


# ---------------------------------------------------------------- learning

def _logloss(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def recalibrate(con):
    """Refit how much to trust our model vs the market, from every graded league-wide prediction.

    A logistic regression of the real outcome on logit(our probability) and logit(market fair probability).
    The new fit is adopted only if it beats the current method on the most recent 20% of games (held out).
    """
    rows = [r for r in con.execute("""SELECT us_date, p_model, fair_over, line, actual, mean, minutes, actual_min
                                      FROM predictions WHERE actual IS NOT NULL AND actual >= 0 ORDER BY us_date""")
            if r["actual"] != r["line"]]
    n = len(rows)
    stats = {"graded_predictions": n}
    if n:
        err = np.array([r["actual"] - r["mean"] for r in rows])
        min_err = np.array([r["actual_min"] - r["minutes"] for r in rows if r["actual_min"]])
        y = np.array([1.0 if r["actual"] > r["line"] else 0.0 for r in rows])
        pm = np.array([r["p_model"] for r in rows])
        pf = np.array([r["fair_over"] for r in rows])
        cur = np.array([final_probability(con, a, b) for a, b in zip(pm, pf)])
        stats.update({"bias": round(float(err.mean()), 2), "minutes_bias": round(float(min_err.mean()), 2) if len(min_err) else None,
                      "logloss_model": round(_logloss(pm, y), 4), "logloss_market": round(_logloss(pf, y), 4),
                      "logloss_current": round(_logloss(cur, y), 4)})
    if n < MIN_CALIBRATION_N:
        stats["status"] = f"collecting ({n}/{MIN_CALIBRATION_N} graded projections before the first refit)"
        set_setting(con, "learning_stats", stats)
        con.commit()
        return stats
    X = np.column_stack([np.ones(n), [logit(p) for p in pm], [logit(p) for p in pf]])
    cut = int(n * 0.8)
    beta = _fit_logistic(X[:cut], y[:cut])
    p_new = 1 / (1 + np.exp(-X[cut:] @ beta))
    new_loss, cur_loss = _logloss(p_new, y[cut:]), _logloss(cur[cut:], y[cut:])
    stats.update({"holdout_new": round(new_loss, 4), "holdout_current": round(cur_loss, 4)})
    if new_loss < cur_loss - 0.001:
        beta = _fit_logistic(X, y)                               # refit on everything once it proved itself
        set_setting(con, "calibration", {"a": float(beta[0]), "b": float(beta[1]), "c": float(beta[2]), "n": n})
        stats["status"] = (f"adopted new weights: model {beta[1]:.2f}, market {beta[2]:.2f} "
                           f"(held-out log loss {new_loss:.4f} vs {cur_loss:.4f})")
        con.execute("INSERT INTO learning_log VALUES (?,?,?,?)", (now_iso(), "recalibration", stats["status"], json.dumps(stats)))
    else:
        stats["status"] = f"kept current weights (new fit {new_loss:.4f} not better than {cur_loss:.4f} on held-out games)"
    set_setting(con, "learning_stats", stats)
    con.commit()
    return stats


def _fit_logistic(X, y, ridge=1.0, iters=50):
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ beta))
        w = p * (1 - p)
        H = X.T @ (X * w[:, None]) + ridge * np.eye(X.shape[1])
        g = X.T @ (y - p) - ridge * beta
        step = np.linalg.solve(H, g)
        beta += step
        if np.abs(step).max() < 1e-6:
            break
    return beta


# ---------------------------------------------------------------- track record

def record(con):
    rows = [dict(r) for r in con.execute("SELECT * FROM picks WHERE status='graded' ORDER BY tip_utc DESC")]
    w = sum(r["result"] == "W" for r in rows)
    l = sum(r["result"] == "L" for r in rows)
    p = sum(r["result"] == "P" for r in rows)
    staked = sum(r["units"] for r in rows if r["result"] in ("W", "L"))
    profit = sum(r["profit"] or 0 for r in rows)
    clv = [r["clv"] for r in rows if r["clv"] is not None]
    by_cat = {}
    for r in rows:
        if r["result"] == "L":
            by_cat[r["miss_category"]] = by_cat.get(r["miss_category"], 0) + 1
    return {"rows": rows, "w": w, "l": l, "p": p, "units": round(profit, 2),
            "roi": round(100 * profit / staked, 1) if staked else None,
            "clv": round(100 * sum(clv) / len(clv), 1) if clv else None, "misses": by_cat}
