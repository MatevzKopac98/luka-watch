"""Offline tests for the parts that break silently. Run: python -m pytest -q"""
import base64
import gzip
import json
import sys
from datetime import date
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                   # noqa: E402
from fetchers import pbp                    # noqa: E402
from model import betting, crypto, props    # noqa: E402
from model import projections as pj         # noqa: E402
from scripts import prune, status           # noqa: E402


# ------------------------------------------------------------------ encryption

def test_encrypt_round_trip_and_wrong_password():
    blob = crypto.encrypt(b"secret picks", "pw-1")
    assert crypto.decrypt(blob, "pw-1") == b"secret picks"
    with pytest.raises(Exception):
        crypto.decrypt(blob, "pw-2")


def test_page_payload_opens_with_fixed_salt():
    salt = b"s" * 16
    p = json.loads(crypto.page_payload("<p>hi</p>", "pw", salt))
    assert base64.b64decode(p["salt"]) == salt and p["iter"] == crypto.ITERATIONS
    key = crypto._key("pw", salt)                                  # what the browser derives with WebCrypto
    html = AESGCM(key).decrypt(base64.b64decode(p["iv"]), base64.b64decode(p["ct"]), None)
    assert html.decode() == "<p>hi</p>"


# ------------------------------------------------------------------ odds parsing

def test_american_to_decimal():
    assert props.american_to_decimal("+150") == 2.5
    assert props.american_to_decimal("-200") == 1.5
    assert props.american_to_decimal("0") is None and props.american_to_decimal(None) is None


def _prop(seq, dec, line="24.5"):
    return {"market": "Total Points", "athlete_id": "1", "event_id": "9", "line": line, "seq": str(seq),
            "decimal": str(dec), "line_open": "23.5", "odds": "-110", "captured_at": "2026-11-04T18:00"}


def test_markets_first_row_is_over_and_margin_removed():
    m = props.markets([_prop(2, 2.2), _prop(1, 1.8)])[("9", "1", "pts")]
    assert m["over_dec"] == 1.8 and m["under_dec"] == 2.2          # lower seq = over
    assert abs(m["fair_over"] - (1 / 1.8) / (1 / 1.8 + 1 / 2.2)) < 1e-9
    assert m["margin"] > 0 and m["line"] == 24.5


def test_markets_skip_unpaired():
    assert props.markets([_prop(1, 1.9)]) == {}


def test_prob_over_behaves():
    assert pj.prob_over(10.5, 25, 6) > 0.99
    assert pj.prob_over(40.5, 25, 6) < 0.02
    assert pj.prob_over(20.5, 25, 6) > pj.prob_over(25.5, 25, 6) > pj.prob_over(30.5, 25, 6)


# ------------------------------------------------------------------ stakes and bankroll

def test_units_quarter_kelly_bounds():
    assert betting.units_for({"p_final": 0.50, "dec": 1.91}) == 0.5   # no edge -> minimum stake
    assert betting.units_for({"p_final": 0.80, "dec": 2.00}) == 2.0   # huge edge -> capped
    u = betting.units_for({"p_final": 0.58, "dec": 1.91})
    assert 0.5 <= u <= 2.0 and (u * 2) == int(u * 2)


@pytest.fixture
def bet_con(tmp_path, monkeypatch):
    monkeypatch.setattr(betting, "DB_PATH", tmp_path / "betting.sqlite")
    return betting.connect()


def _pick(con, pid, day, units, dec, result, status="graded"):
    con.execute("INSERT INTO picks (pick_id, us_date, tip_utc, units, dec, result, status) VALUES (?,?,?,?,?,?,?)",
                (pid, day, day + "T23:00Z", units, dec, result, status))


def test_bankroll_stakes_from_start_of_day(bet_con):
    _pick(bet_con, "a", "20261101", 1, 2.0, "W")      # day 1: 1% of 100 = 1.00, wins 1.00
    _pick(bet_con, "b", "20261101", 2, 1.9, "L")      # same day, still from 100: 2.00 lost
    _pick(bet_con, "c", "20261102", 1, 1.9, "P")      # push: stake back
    _pick(bet_con, "d", "20261103", 1, 3.0, None, "void")
    b = betting.bankroll(bet_con)
    assert b["picks"]["a"] == {"stake": 1.0, "profit": 1.0, "after": 101.0}
    assert b["picks"]["b"] == {"stake": 2.0, "profit": -2.0, "after": 99.0}
    assert b["picks"]["c"]["profit"] == 0 and b["picks"]["d"]["profit"] == 0
    assert b["value"] == 99.0 and b["curve"][0] == ("start", 100.0)
    assert betting.stake_now(bet_con, 2) == 1.98


def test_record_counts(bet_con):
    for i, r in enumerate("WWLP"):
        _pick(bet_con, str(i), "20261101", 1, 1.9, r)
    bet_con.execute("UPDATE picks SET profit = CASE result WHEN 'W' THEN 0.9 WHEN 'L' THEN -1 ELSE 0 END")
    rec = betting.record(bet_con)
    assert (rec["w"], rec["l"], rec["p"]) == (2, 1, 1)
    assert rec["units"] == 0.8 and rec["roi"] == round(100 * 0.8 / 3, 1)


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


# ------------------------------------------------------------------ snapshot pruning

def test_keep_rules():
    f = [f"{i}" for i in range(5)]
    assert prune.keep(f, 1) == f
    assert prune.keep(f, 10) == ["0", "3", "4"]
    assert prune.keep(f, 60) == ["4"]
    assert prune.keep(["only"], 10) == ["only"]


def test_prune_files(tmp_path):
    for kind in ("odds", "props"):
        (tmp_path / kind).mkdir()
        for day in ("20261101", "20261020"):
            for hhmm in ("1623", "2023", "0013", "0313"):
                with gzip.open(tmp_path / kind / f"{day}_{hhmm}.csv.gz", "wt") as fh:
                    fh.write("x\n")
    n, _ = prune.prune(tmp_path, today=date(2026, 11, 3))
    assert n == 2                                   # 20261020 (14 days old): drops 1 of 4 in each folder
    left = sorted(p.name for p in (tmp_path / "props").iterdir())
    assert left.count("20261101_1623.csv.gz") == 1 and len(left) == 7


# ------------------------------------------------------------------ morning checks

def test_status_checks_flag_missing_data(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "nba.sqlite")
    con = db.connect()
    w = status.checks(con, "2026-11-04", ["1", "2"], (3, 0, 0))
    text = " ".join(w)
    assert "0 of 30 teams" in text and "MVP odds" in text and "2 of last night's 2" in text and "Odds:" in text
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
