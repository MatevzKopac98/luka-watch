"""Build the inputs for model.trades from the site's data (rosters, contracts, impact ratings, minutes)."""
from model.trades import P


def per36(line):
    if not line or not line.get("min"):
        return {}
    k = 36 / line["min"]
    return {s: line.get(s, 0) * k for s in ("reb", "blk", "stl", "tov", "fg3m", "fg3a", "ast", "pts")}


def build(roster_all, bb_of, years_of, impact, cur, last, injuries):
    """{athlete_id: P}. Salary counts only contracts in the Basketball-Reference export (two-way deals = 0)."""
    out_ids = {i.get("athlete_id") for i in injuries if (i.get("status") or "").lower() == "out"}
    players = {}
    for r in roster_all:
        pid = r["athlete_id"]
        line_c, line_l = cur.get(pid), last.get(pid)
        line = line_c if line_c and line_c["gp"] >= 3 else line_l if line_l and line_l["gp"] >= 10 else line_c
        mpg = line["min"] / line["gp"] if line and line["gp"] else 6.0
        imp = impact.get(pid) or {}
        c = bb_of.get(pid)
        players[pid] = P(pid=pid, name=r["name"], team=r["team_id"], pos=r.get("position") or "",
                         age=r.get("age") or 0, salary=float(c["cost"]) if c else 0.0,
                         years=(years_of(r) or 1) if c else 0, off=imp.get("off_impact") or 0.0,
                         dfn=imp.get("def_impact") or 0.0, poss=imp.get("def_poss") or 0.0, mpg=mpg,
                         out=pid in out_ids, stats=per36(line))
    return players
