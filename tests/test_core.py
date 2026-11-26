"""Offline tests for the parts that break silently. Run: python -m pytest -q"""
import gzip
import json
import sys
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                   # noqa: E402
from fetchers import pbp                    # noqa: E402
from scripts import status                  # noqa: E402


# ------------------------------------------------------------------ lineup stints

def _summary():
    home = [str(i) for i in range(1, 7)]        # 1-5 start, 6 comes off the bench
    away = [str(i) for i in range(11, 17)]
    box = lambda tid, ids: {"team": {"id": tid}, "statistics": [{"athletes": [
        {"athlete": {"id": a}, "starter": n < 5} for n, a in enumerate(ids)] + [{"athlete": {}}]}]}
    plays, score = [], [0, 0]

    def play(period, clock, kind, team=None, ids=(), shot=False, pts=0):
        if pts:
            score[0 if team == "H" else 1] += pts
        plays.append({"period": {"number": period}, "clock": {"displayValue": clock}, "type": {"text": kind},
                      "team": {"id": team} if team else None, "shootingPlay": shot,
                      "participants": [{"athlete": {"id": i}} for i in ids],
                      "homeScore": score[0], "awayScore": score[1]})
    play(1, "11:40", "Jump Shot", "H", ["1"], shot=True, pts=2)
    play(1, "10:00", "Offensive Rebound", "A", [])                 # team rebound: not counted
    play(1, "6:00", "Substitution", "H", ["6", "5"])               # 6 in for 5 at 6:00 of Q1
    play(1, "5:00", "Three Point Jumper", "A", ["11"], shot=True, pts=3)
    for q in (2, 3, 4):
        play(q, "11:00", "Jump Shot", "H", ["1"], shot=True, pts=2)
    play(5, "4:00", "Free Throw 1 of 2", "A", ["12"], pts=1)        # an overtime
    return {"header": {"id": "777", "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"id": "H"}}, {"homeAway": "away", "team": {"id": "A"}}]}]},
        "boxscore": {"players": [box("H", home), box("A", away)]}, "plays": plays}


def test_build_stints_covers_game_and_tracks_subs():
    stints, quality = pbp.build_stints(_summary())
    assert sum(s["seconds"] for s in stints) == 4 * 720 + 300
    assert quality == 1.0
    first, second = stints[0], stints[1]
    assert first["seconds"] == 360 and "5" in first["home_ids"].split("|")
    assert "6" in second["home_ids"].split("|") and "5" not in second["home_ids"].split("|")
    assert first["home_pts"] == 2 and first["away_oreb"] == 0
    assert second["away_pts"] == 3 and second["away_fga"] == 1
    assert sum(s["home_pts"] for s in stints) == 8 and sum(s["away_pts"] for s in stints) == 4
    assert stints[-1]["period"] == 5 and stints[-1]["away_fta"] == 1


def test_secs_formats():
    assert pbp._secs("7:04") == 424 and pbp._secs("45.3") == 45.3 and pbp._secs("0:07:04") == 424


# ------------------------------------------------------------------ morning checks

def test_status_checks_flag_missing_data(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "nba.sqlite")
    con = db.connect()
    w = status.checks(con, "2026-11-04", ["1", "2"])
    text = " ".join(w)
    assert "0 of 30 teams" in text and "MVP odds" in text and "2 of last night's 2" in text
    out = status.write(["teams: boom"], w, tmp_path / "status.json")
    assert status.read(tmp_path / "status.json") == out


# ------------------------------------------------------------------ lineups and rotation

def _stint(start, secs, home_pts, away_pts, home="1|2|3|4|5", period=1):
    z = {f"{s}_{k}": 10 for s in ("home", "away") for k in ("fga",)}
    z.update({f"{s}_{k}": 0 for s in ("home", "away") for k in ("oreb", "tov", "fta")})
    return {"event_id": "g1", "period": period, "start_sec": start, "seconds": secs, "home_ids": home,
            "away_ids": "11|12|13|14|15", "home_pts": home_pts, "away_pts": away_pts, **z}


def test_lineups_ratings_and_rotation_shares():
    from model import lineups as lu
    games = {"g1": ("H", "A")}
    stints = [_stint(0, 360, 12, 8), _stint(360, 360, 5, 10, home="1|2|3|4|6")]
    units = lu.lineups(stints, games)
    first = units["H"][0]
    assert first["ids"] == ["1", "2", "3", "4", "5"] and first["minutes"] == 6
    assert first["ortg"] == 120 and first["drtg"] == 80 and first["net"] == 40     # 10 possessions each
    assert units["A"][0]["minutes"] == 12
    rot, n = lu.rotation(stints, games)
    assert n["H"] == 1
    assert rot["H"]["5"][:4] == [1.0, 1.0, 0.0, 0.0]          # first 6 minutes = two 3-minute stretches
    assert rot["H"]["6"][:4] == [0.0, 0.0, 1.0, 1.0]


# ------------------------------------------------------------------ Basketball-Reference contract exports

def _export(tmp_path, name, head, rows):
    cells = "".join(f'<th data-stat="{k}">{v}</th>' for k, v in head)
    body = "".join("<tr>" + "".join(f'<td data-stat="{k}"{a}>{v}</td>' for k, v, a in r) + "</tr>" for r in rows)
    p = tmp_path / name
    p.write_text(f"<table><thead><tr>{cells}</tr></thead><tbody>{body}</tbody></table>", encoding="utf-8")
    return p


def test_contract_import_reconciles_waived_players(tmp_path):
    from scripts import import_contracts as ic
    head = [("player", "Player"), ("team_id", "Tm"), ("y1", "2026-27"), ("y2", "2027-28"), ("remain_gtd", "Gtd")]
    pl = lambda n, i, t, y1, g: [("player", n, f' data-append-csv="{i}"'), ("team_id", t, ""), ("y1", y1, ""),
                                 ("y2", "", ""), ("remain_gtd", g, "")]
    players = _export(tmp_path, "p.xls", head, [
        pl("Star Guy", "starg01", "LAL", "$40,000,000", "$40,000,000"),
        pl("Old Vet", "oldve01", "MIL", "$30,000,000", "$20,000,000"),     # waived by MIL ...
        pl("Old Vet", "oldve01", "LAL", "$30,000,000", "$10,000,000"),     # ... signed by LAL: listed twice
        pl("Star Guy", "starg01", "LAL", "$40,000,000", "$40,000,000"),    # exact repeat row
        pl("Bench Guy", "bench01", "MIL", "$5,000,000", "$5,000,000")])
    teams = _export(tmp_path, "t.xls", [("team_name", "Team"), ("y1", "2026-27")], [
        [("team_name", "Los Angeles Lakers", ""), ("y1", "$50,000,000", "")],
        [("team_name", "Milwaukee Bucks", ""), ("y1", "$25,000,000", "")]])
    assert ic.kind(players) == "players" and ic.kind(teams) == "teams"
    s = ic.build(players, teams, {"LAL": "Los Angeles Lakers", "MIL": "Milwaukee Bucks"})
    assert s["season"] == "2026-27" and all(c["diff"] == 0 for c in s["checks"])
    cost = {(p["name"], p["team"]): p["cost"] for p in s["players"]}
    assert cost[("Old Vet", "LAL")] == 10_000_000 and cost[("Old Vet", "MIL")] == 20_000_000
    roster = [{"name": "Star Guy", "abbr": "LAL"}, {"name": "Old Vet", "abbr": "LAL"},
              {"name": "Two Way", "abbr": "LAL"}, {"name": "Bench Guy", "abbr": "LAL"}]
    m = ic.match(s, roster)
    assert [m[i][0] for i in range(4)] == ["ok", "ok", "none", "elsewhere"]


# ------------------------------------------------------------------ trade ideas

def test_salary_matching_tiers_and_aprons():
    from model import trades as tr
    over_cap = 190_000_000
    assert tr.allowed_incoming(5e6, over_cap, 10.25e6, 1)[0]          # small: 200% + 250K
    assert not tr.allowed_incoming(5e6, over_cap, 10.3e6, 1)[0]
    assert tr.allowed_incoming(20e6, over_cap, 29.1e6, 2)[0]          # middle: + 9.1M
    assert not tr.allowed_incoming(20e6, over_cap, 29.2e6, 2)[0]
    assert tr.allowed_incoming(40e6, 170e6, 50.25e6, 1)[0]            # large: 125% + 250K
    ok, _, rule = tr.allowed_incoming(10e6, 208e6, 12e6, 1)           # would end over the first apron
    assert not ok and "first apron" in rule
    assert not tr.allowed_incoming(10e6, 230e6, 9e6, 2)[0]            # second apron: no combining salaries
    assert tr.allowed_incoming(0, 150e6, 14e6, 0)[0]                  # cap room absorbs


def _pl(pid, mpg, net=0.0, pos="F", salary=5e6, out=False, team="13"):
    from model.trades import P
    return P(pid=pid, name=f"Player {pid}", team=team, pos=pos, age=27, salary=salary, years=2,
             off=net / 2, dfn=-net / 2, poss=3000, mpg=mpg, out=out)


def test_minutes_keep_starters_and_flag_overload():
    from model import trades as tr
    roster = [_pl(str(i), m) for i, m in enumerate([36, 34, 30, 28, 26, 24, 22, 18, 14, 10, 6, 4])]
    mins, ok = tr.allocate(roster)
    assert ok and abs(sum(mins.values()) - 240) < 0.5 and abs(mins["0"] - 36) < 0.1
    thin = [_pl(str(i), 30) for i in range(6)]                       # six players can't cover 240 minutes
    assert tr.allocate(thin)[1] is False


def test_depth_rule_blocks_thin_rotation():
    from model import trades as tr
    before = [_pl(str(i), m, pos="G" if i < 5 else "C" if i < 7 else "F")
              for i, m in enumerate([36, 34, 30, 28, 26, 24, 22, 20, 16, 12, 6, 4, 3, 2])]
    m0, _ = tr.allocate(before)
    after = before[3:] + [_pl("new", 30, pos="G")]                   # three rotation players out, one in
    m1, _ = tr.allocate(after)
    assert tr._depth_problem(before, after, m0, m1) is not None


# ------------------------------------------------------------------ fresh season numbers from box scores

def test_fresh_averages_and_doubles():
    from model import fresh
    g = lambda **k: {**{c: "0" for c in fresh.COUNT}, **{c: str(v) for c, v in k.items()}}
    assert fresh._doubles(g(pts=30, reb=10, ast=9)) == (True, False)
    assert fresh._doubles(g(pts=30, reb=12, ast=11)) == (True, True)
    t = {"gp": 2, "dd2": 1, "td3": 0, "games": [], **{k: 0.0 for k in fresh.COUNT}}
    t.update(min=70, pts=80, fgm=28, fga=50, fg3m=8, fg3a=20, ftm=16, fta=20, reb=18, ast=16)
    a = fresh.averages(t, "x")
    assert a["gp"] == 2 and a["pts"] == 40 and a["fg_pct"] == 56.0 and a["fg3_pct"] == 40.0 and a["ft_pct"] == 80.0
    assert a["ts_pct"] == round(100 * 40 / (2 * (25 + 0.44 * 10)), 1)


def test_add_columns_fills_and_extends_header(tmp_path):
    import csv, gzip
    from scripts.backfill_history import add_columns
    p = tmp_path / "team_box.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event_id", "team_id", "fgm"])
        w.writeheader()
        w.writerows([{"event_id": "1", "team_id": "13", "fgm": 40}, {"event_id": "1", "team_id": "2", "fgm": 38}])
    assert add_columns(p, {("1", "13"): {"fastbreak": 17.0, "paint": 54.0}}, ("fastbreak", "paint")) == 1
    rows = list(csv.DictReader(gzip.open(p, "rt", encoding="utf-8")))
    assert rows[0]["paint"] == "54.0" and rows[0]["fgm"] == "40" and rows[1]["paint"] == ""
    assert add_columns(p, {("1", "13"): {"fastbreak": 17.0, "paint": 54.0}}, ("fastbreak", "paint")) == 0


def test_team_box_maps_paint_and_fastbreak():
    from fetchers import espn
    assert espn.TEAM_BOX["pointsInPaint"] == "paint" and espn.TEAM_BOX["fastBreakPoints"] == "fastbreak"


