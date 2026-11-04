"""Team pages: site/teams/index.html (all 30 teams) and site/teams/<ABBR>.html (one per team).

Each team page: header (record, ratings with ranks, roster outlook, payroll, injuries), players (this season /
last season x per game / per 36 / totals, with salary), five-man lineups, rotation and usage, impact (lineup
model), value for money (salary vs impact) and contracts. Built from the site database plus the stored box
scores; no extra requests.
"""
from html import escape

from db import connect
from model import lineups as lu
from model.team_stats import season_lines, view
from scripts.build_site import LAKERS_ID, OUT, fmt, ordinal, rank

STAT_COLS = [("gp", "GP", 0), ("gs", "GS", 0), ("min", "MIN", 1), ("pts", "PTS", 1), ("reb", "REB", 1),
             ("ast", "AST", 1), ("stl", "STL", 1), ("blk", "BLK", 1), ("tov", "TOV", 1),
             ("fga", "FGA", 1), ("fg_pct", "FG%", 1), ("fg3a", "3PA", 1), ("fg3_pct", "3P%", 1),
             ("fta", "FTA", 1), ("ft_pct", "FT%", 1), ("ts_pct", "TS%", 1), ("plus_minus", "+/-", 1)]
MODES = [("game", "Per game"), ("p36", "Per 36 min"), ("total", "Totals")]
MIN_IMPACT_POSS = 500        # possessions on court before a player's impact rating is shown
MIN_VALUE_POSS = 1000
PAYROLL_COMPLETE = 0.9       # share of a roster with a known salary before its payroll is ranked
LINEUPS_SHOWN = 8
BEST_MIN_MINUTES = {"cur": 15, "last": 60}   # minutes together before a lineup can rank as "best"
ROTATION_PLAYERS = 10
ROTATION_MIN_MPG = 6         # players averaging fewer minutes are left off the rotation chart


def season_toggle():
    return ('<button type="button" class="chip on" data-dim="season" data-val="cur" aria-pressed="true">'
            'This season</button><button type="button" class="chip" data-dim="season" data-val="last" '
            'aria-pressed="false">Last season</button>')


def _short(name):
    parts = (name or "?").split()
    if len(parts) > 2 and parts[-1] in ("Jr.", "Sr.", "II", "III", "IV"):
        return " ".join(parts[-2:])
    return parts[-1]


def lineup_table(units, names, empty):
    if not units:
        return f'<p class="muted small">{empty}</p>'
    rows = []
    for u in units:
        five = " · ".join(escape(_short(names.get(i))) for i in u["ids"])
        full = escape(", ".join(names.get(i, "?") for i in u["ids"]))
        rows.append(f'<tr><th scope="row" class="five" title="{full}">{five}</th><td>{u["minutes"]:.0f}</td>'
                    f'<td>{u["games"]}</td><td>{fmt(u["ortg"])}</td><td>{fmt(u["drtg"])}</td>'
                    f'<td class="strong {_cls(u["net"])}">{u["net"]:+.1f}</td></tr>')
    return ('<div class="tablewrap"><table class="stats lineups"><thead><tr><th scope="col">Lineup</th><th>MIN</th>'
            '<th>GP</th><th>ORtg</th><th>DRtg</th><th>Net</th></tr></thead><tbody>' + "".join(rows)
            + '</tbody></table></div>')


def lineups_card(team_id, seasons, names):
    panels = []
    for key, data in seasons.items():
        units = [u for u in data["lineups"].get(team_id, []) if u["net"] is not None]
        best = sorted((u for u in units if u["minutes"] >= BEST_MIN_MINUTES[key]), key=lambda u: -u["net"])
        options = (("used", units[:LINEUPS_SHOWN], "No lineup data yet."),
                   ("best", best[:LINEUPS_SHOWN], f"No lineup has played {BEST_MIN_MINUTES[key]} minutes together yet."))
        for mode, chosen, empty in options:
            hidden = "" if (key, mode) == ("cur", "used") else " hidden"
            panels.append(f'<div class="combo-panel" data-key="{key}-{mode}"{hidden}>'
                          f'{lineup_table(chosen, names, empty)}</div>')
    mode_chips = ('<button type="button" class="chip on" data-dim="mode" data-val="used" aria-pressed="true">'
                  'Most used</button><button type="button" class="chip" data-dim="mode" data-val="best" '
                  'aria-pressed="false">Best</button>')
    return f"""
<div class="card combo" data-season="cur" data-mode="used">
  <h3>Five-man lineups</h3>
  <div class="chip-rows"><div class="chips" role="group" aria-label="Season">{season_toggle()}</div>
  <div class="chips" role="group" aria-label="Sort">{mode_chips}</div></div>
  {''.join(panels)}
  <p class="muted small">Every stretch with the same five on the floor, from play-by-play. ORtg/DRtg = points scored/allowed
  per 100 possessions with that lineup; Net = the difference. "Best" ranks lineups with at least
  {BEST_MIN_MINUTES['cur']} minutes together this season ({BEST_MIN_MINUTES['last']} last season); small samples swing a lot.</p>
</div>"""


def rotation_grid(team_id, data, names):
    shares = data["rotation"].get(team_id, {})
    players = sorted(((v, pid) for (t, pid), v in data["usage"].items()
                      if t == team_id and v["min"] >= ROTATION_MIN_MPG and pid in shares), key=lambda x: -x[0]["min"])
    if not players:
        return '<p class="muted small">No games yet.</p>'
    head = "".join(f'<span class="q">Q{q}</span>' for q in range(1, 5))
    rows = []
    for v, pid in players[:ROTATION_PLAYERS]:
        name = names.get(pid) or v["name"]
        cells = []
        for b, x in enumerate(shares[pid]):
            q, start = b // 4 + 1, 12 - 3 * (b % 4)
            cls = ' class="qs"' if b and b % 4 == 0 else ""
            cells.append(f'<i{cls} style="--v:{round(100 * x)}%" data-tip="{escape(name)}|Q{q}, {start}:00 to '
                         f'{start - 3}:00|on court in {round(100 * x)}% of his games"></i>')
        usg = f'{v["usg"]:.0f}%' if v["usg"] is not None else "–"
        rows.append(f'<span class="rname">{escape(_short(name))}<small>{v["min"]:.0f} min · {v["gs"]}/{v["gp"]} '
                    f'starts · usage {usg}</small></span>{"".join(cells)}')
    return f'<div class="rot"><span></span>{head}{"".join(rows)}</div>'


def rotation_card(team_id, seasons, names):
    panels = "".join(f'<div class="combo-panel" data-key="{key}-x"{"" if key == "cur" else " hidden"}>'
                     f'{rotation_grid(team_id, data, names)}</div>' for key, data in seasons.items())
    return f"""
<div class="card combo" data-season="cur" data-mode="x">
  <h3>Rotation &amp; usage</h3>
  <div class="chips" role="group" aria-label="Season">{season_toggle()}</div>
  {panels}
  <p class="muted small">Darker = on the court more often in that 3-minute stretch of regulation (share of the games he
  played), which shows who starts, who closes and when the bench comes in. Usage = share of the team's plays he finishes
  with a shot, free throws or a turnover while on court (about 20% is average; stars run 30% or more).</p>
</div>"""


def money(v):
    if v is None:
        return "–"
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}K"


def player_cell(r, sub=""):
    img = f'<img src="{r["headshot"]}" alt="" class="mini">' if r.get("headshot") else ""
    meta = " · ".join(x for x in (r.get("position") or "", f'{r["age"]}' if r.get("age") else "", sub) if x)
    return f'<th scope="row">{img}<span>{escape(r["name"] or "")}<small>{escape(meta)}</small></span></th>'


def players_table(roster, lines, mode, salary_key, abbr_of, team_id, last):
    rows = []
    ordered = sorted(roster, key=lambda r: -(lines.get(r["athlete_id"], {}).get("min") or 0))
    for r in ordered:
        line = lines.get(r["athlete_id"])
        v = view(line, mode)
        team_note = ""
        if last and line and line["team_id"] != team_id:
            team_note = "with " + "/".join(abbr_of.get(t, "?") for t in line["teams"])
        sal = money(r.get(salary_key))
        if not v:
            rows.append(f'<tr class="dim">{player_cell(r, team_note)}<td>{sal}</td>'
                        f'<td colspan="{len(STAT_COLS)}" class="muted">no games</td></tr>')
            continue
        cells = []
        for k, _, dd in STAT_COLS:
            val = v.get(k)
            if k == "plus_minus" and val is not None:
                cells.append(f'<td class="{"pos" if val > 0 else "neg" if val < 0 else ""}">{val:+.{dd}f}</td>')
            elif k in ("gp", "gs"):
                cells.append(f"<td>{int(val)}</td>")
            else:
                cells.append(f"<td>{fmt(val, dd if mode != 'total' or k.endswith('pct') else 0)}</td>")
        rows.append(f'<tr>{player_cell(r, team_note)}<td>{sal}</td>{"".join(cells)}</tr>')
    head = "".join(f"<th>{lbl}</th>" for _, lbl, _ in STAT_COLS)
    return (f'<div class="tablewrap"><table class="stats roster"><thead><tr><th scope="col">Player</th>'
            f'<th>Salary</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def players_card(team, roster, cur, last, abbr_of):
    panels = []
    for season_key, lines, salary_key, is_last in (("cur", cur, "salary", False), ("last", last, "salary_last", True)):
        for mode, _ in MODES:
            hidden = "" if (season_key, mode) == ("cur", "game") else " hidden"
            panels.append(f'<div class="combo-panel" data-key="{season_key}-{mode}"{hidden}>'
                          f'{players_table(roster, lines, mode, salary_key, abbr_of, team["team_id"], is_last)}</div>')
    season_chips = ('<button type="button" class="chip on" data-dim="season" data-val="cur" aria-pressed="true">'
                    'This season</button><button type="button" class="chip" data-dim="season" data-val="last" '
                    'aria-pressed="false">Last season</button>')
    mode_chips = "".join(f'<button type="button" class="chip{" on" if i == 0 else ""}" data-dim="mode" '
                         f'data-val="{m}" aria-pressed="{"true" if i == 0 else "false"}">{lbl}</button>'
                         for i, (m, lbl) in enumerate(MODES))
    return f"""
<div class="card combo" data-season="cur" data-mode="game">
  <h3>Players</h3>
  <div class="chip-rows"><div class="chips" role="group" aria-label="Season">{season_chips}</div>
  <div class="chips" role="group" aria-label="Stat view">{mode_chips}</div></div>
  {''.join(panels)}
  <p class="muted small">Today's roster. Last season shows each player's numbers wherever he played ("with ..." when it was
  another team). Salary is the season shown. TS% = true shooting (scoring efficiency including threes and free throws).</p>
</div>"""


def impact_card(roster, impact):
    rows = []
    for r in roster:
        i = impact.get(r["athlete_id"])
        if not i or (i["def_poss"] or 0) < MIN_IMPACT_POSS:
            continue
        o, dfn = i["off_impact"] or 0, -(i["def_impact"] or 0)        # defence flipped: positive = better
        rows.append((o + dfn, r, o, dfn, i))
    rows.sort(key=lambda x: -x[0])
    body = "".join(
        f'<tr>{player_cell(r)}<td class="{_cls(o)}">{o:+.1f}</td><td class="{_cls(d)}">{d:+.1f}</td>'
        f'<td class="strong {_cls(net)}">{net:+.1f}</td><td>{fmt(i["on_ortg"])}</td><td>{fmt(i["on_drtg"])}</td>'
        f'<td>{i["def_poss"]:,.0f}</td></tr>' for net, r, o, d, i in rows)
    return f"""
<div class="card"><h3>Impact (lineup model)</h3>
  <div class="tablewrap"><table class="stats impact-t"><thead><tr><th scope="col">Player</th><th>Offense</th>
  <th>Defense</th><th>Net</th><th>Team ORtg on court</th><th>Team DRtg on court</th><th>Possessions</th></tr></thead>
  <tbody>{body or '<tr><td colspan="7" class="muted">Not enough minutes yet.</td></tr>'}</tbody></table></div>
  <p class="muted small">Points per 100 possessions a player adds on offense and saves on defense, separated from the
  teammates and opponents he shared the court with (last three seasons, recent ones weighted more). Positive = better on
  both. On-court ratings are the team's points scored/allowed per 100 possessions while he plays. Players with fewer than
  {MIN_IMPACT_POSS} possessions aren't rated.</p>
</div>"""


def _cls(v):
    return "pos" if v > 0.3 else "neg" if v < -0.3 else ""


def value_card(roster, impact, salary_of):
    pts = []
    for r in roster:
        i, sal = impact.get(r["athlete_id"]), salary_of(r)
        if i and sal and (i["def_poss"] or 0) >= MIN_VALUE_POSS:
            pts.append((sal, (i["off_impact"] or 0) - (i["def_impact"] or 0), r))
    if len(pts) < 3:
        return '<div class="card"><h3>Value for money</h3><p class="muted">Not enough rated players with salaries yet.</p></div>'
    W, H, L, R, T, B = 560, 300, 44, 16, 14, 36
    xs, ys = [p[0] / 1e6 for p in pts], [p[1] for p in pts]
    x0, x1 = 0, max(xs) * 1.08
    y0, y1 = min(ys + [0]) - 1, max(ys + [0]) + 1
    x = lambda v: L + (W - L - R) * (v - x0) / (x1 - x0)
    y = lambda v: T + (H - T - B) * (1 - (v - y0) / (y1 - y0))
    zero = f'<line x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="ref"/>'
    ticks = "".join(f'<text x="{x(v):.1f}" y="{H - 18}" class="tick" text-anchor="middle">${v:.0f}M</text>'
                    for v in range(0, int(x1) + 1, max(5, int(x1 / 5) // 5 * 5 or 5)))
    marks = ""
    for sal, net, r in pts:
        cx, cy = x(sal / 1e6), y(net)
        cls = "dot-hi" if net > 0 else "dot-opp"
        marks += (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" class="{cls}"/>'
                  f'<text x="{cx + 8:.1f}" y="{cy + 4:.1f}" class="tick">{escape((r["name"] or "").split()[-1])}</text>'
                  f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="12" class="hit" data-tip="'
                  f'{escape(r["name"] or "")}|{money(sal)} · net impact {net:+.1f}" tabindex="0"/>')
    axis = (f'<text x="{(L + W - R) / 2:.1f}" y="{H - 2}" class="tick" text-anchor="middle">Salary this season →</text>'
            f'<text x="12" y="{(T + H - B) / 2:.1f}" class="tick" text-anchor="middle" '
            f'transform="rotate(-90 12 {(T + H - B) / 2:.1f})">Net impact →</text>')
    per10 = sorted(((net / (sal / 1e7), sal, net, r) for sal, net, r in pts if net > 0), key=lambda t: -t[0])
    pricey = sorted(((net, sal, r) for sal, net, r in pts if sal >= 1e7), key=lambda t: t[0] / (t[1] / 1e7))
    best = "".join(f"<li><strong>{escape(r['name'])}</strong> · {money(s)} · net {n:+.1f} "
                   f"<span class='muted small'>({v:+.1f} per $10M)</span></li>" for v, s, n, r in per10[:3])
    worst = "".join(f"<li><strong>{escape(r['name'])}</strong> · {money(s)} · net {n:+.1f}</li>" for n, s, r in pricey[:3])
    return f"""
<div class="card"><h3>Value for money</h3>
  <svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Salary vs impact">{zero}{ticks}{axis}{marks}</svg>
  <div class="grid2 tight"><div><p class="lbl-k">Best value</p><ul class="results">{best or '<li class="muted">–</li>'}</ul></div>
  <div><p class="lbl-k">Least impact for the money ($10M+)</p><ul class="results">{worst or '<li class="muted">–</li>'}</ul></div></div>
  <p class="muted small">Net impact from the lineup model vs this season's salary. Above the dashed line = makes the team
  better. Players with fewer than {MIN_VALUE_POSS} possessions over three seasons aren't shown.</p>
</div>"""


def contracts_card(roster, salary_of, years_of, payroll, pay):
    rows = sorted(((salary_of(r) or 0, r) for r in roster), key=lambda t: -t[0])
    body = "".join(
        f'<tr>{player_cell(r)}<td class="strong">{money(s) if s else "–"}</td>'
        f'<td><span class="rbar wide-bar"><i style="width:{100 * s / payroll if payroll else 0:.0f}%"></i></span> '
        f'{100 * s / payroll if payroll else 0:.0f}%</td><td>{years_of(r) if years_of(r) is not None else "–"}</td></tr>'
        for s, r in rows)
    return f"""
<div class="card"><h3>Contracts</h3>
  <p class="verdict"><strong>{escape(pay[2])}</strong></p>
  <div class="tablewrap"><table class="stats contracts"><thead><tr><th scope="col">Player</th><th>Salary</th>
  <th>Share of payroll</th><th>Years left</th></tr></thead><tbody>{body}</tbody></table></div>
  <p class="muted small">Salaries and contract years from ESPN; "–" = not published by ESPN yet (often new signings
  and rookies), filled in automatically once it is. Shares are of the known payroll.</p>
</div>"""


def header(team, d, ranks, proj, pay, injuries):
    p = proj.get(team["team_id"]) or {}
    tiles = [("Record", f"{team['wins']}-{team['losses']}", f"{ordinal(team['seed'])} in the {team['conference']}"),
             ("Streak", team["streak"] or "–", f"Last 10: {team['last10']}"),
             ("Offense", fmt(team["ortg"]), f"{ordinal(ranks['ortg'][team['team_id']])} in NBA"),
             ("Defense", fmt(team["drtg"]), f"{ordinal(ranks['drtg'][team['team_id']])} in NBA"),
             ("Net rating", f"{team['net_rtg']:+.1f}", f"{ordinal(ranks['net_rtg'][team['team_id']])} in NBA"),
             ("Payroll", pay[0], pay[1])]
    tiles_html = "".join(f'<div class="tile"><div class="tile-k">{k}</div><div class="tile-v">{v}</div>'
                         f'<div class="tile-s">{s}</div></div>' for k, v, s in tiles)
    outlook = ""
    if p:
        outlook = (f'<p class="muted small">Roster outlook (lineup model, healthy players): offense {fmt(p["roster_ortg"])}, '
                   f'defense {fmt(p["roster_drtg"])} points per 100 possessions vs last season '
                   f'{fmt(p["last_season_ortg"])} / {fmt(p["last_season_drtg"])}.</p>')
    inj = "".join(f'<li><strong>{escape(i["player"] or "")}</strong> · {escape(i["status"] or "")}'
                  f'{" — " + escape(i["detail"]) if i["detail"] else ""}</li>' for i in injuries)
    return f"""
<section class="hero">
  <div class="hero-head"><img src="{team['logo']}" alt="" class="biglogo"><div><h1>{escape(team['name'])}</h1>
  <p class="muted"><a href="index.html">All teams</a> · {team['conference']}ern Conference</p></div></div>
  <div class="tiles">{tiles_html}</div>
  {outlook}
  {f'<details class="list"><summary>Injuries ({len(injuries)})</summary><ul class="inj">{inj}</ul></details>' if injuries else ''}
</section>"""


def payroll_text(total, cov, rank_, n_ranked):
    """(tile value, tile subtitle, one-line summary) with an honest note when salaries are incomplete."""
    known, n = cov
    if n and known / n >= PAYROLL_COMPLETE:
        sub = f"{ordinal(rank_)} highest of {n_ranked} with full data" if rank_ else ""
        return money(total), sub, f"Payroll {money(total)}" + (f" · {sub}" if sub else "")
    return (f"{money(total)}+", f"known for {known} of {n} players",
            f"Known payroll {money(total)} ({known} of {n} players): ESPN hasn't published every new contract yet")


def build_teams(d, page):
    con = connect()
    roster_all = [dict(r) for r in con.execute("SELECT * FROM roster_players")]
    contracts = {r["athlete_id"]: dict(r) for r in con.execute("SELECT * FROM contracts")}
    impact = {r["athlete_id"]: dict(r) for r in con.execute("SELECT * FROM player_defense")}
    con.close()
    if not roster_all:
        return 0
    season = d["season"] or 0
    cur, last = season_lines(season), season_lines(season - 1)
    seasons, names = {}, {}
    for key, yr in (("cur", season), ("last", season - 1)):
        stints, games = lu.load(yr)
        rot, _ = lu.rotation(stints, games)
        seasons[key] = {"lineups": lu.lineups(stints, games), "rotation": rot, "usage": lu.usage(yr)}
        for (_, pid), v in seasons[key]["usage"].items():
            names.setdefault(pid, v["name"])
    names.update({r["athlete_id"]: r["name"] for r in roster_all if r.get("name")})
    teams = {t["team_id"]: t for t in d["teams"]}
    abbr_of = {t["team_id"]: t["abbr"] for t in d["teams"]}
    ranks = {k: rank(d["teams"], k, low) for k, low in (("ortg", False), ("drtg", True), ("net_rtg", False))}

    def salary_of(r):
        c = contracts.get(r["athlete_id"])
        return (c or {}).get("salary") or r.get("salary")

    def years_of(r):
        return (contracts.get(r["athlete_id"]) or {}).get("years_remaining")

    payroll, coverage = {}, {}
    for t in teams:
        mine = [r for r in roster_all if r["team_id"] == t]
        known = [salary_of(r) for r in mine if salary_of(r)]
        payroll[t], coverage[t] = sum(known), (len(known), len(mine))
    # Rank payrolls only among teams whose salaries are (nearly) complete; ESPN lags on new deals
    complete = [t for t in teams if coverage[t][1] and coverage[t][0] / coverage[t][1] >= PAYROLL_COMPLETE]
    pay_rank = {t: i for i, t in enumerate(sorted(complete, key=lambda t: -payroll[t]), 1)}
    out_dir = OUT.parent / "teams"
    out_dir.mkdir(exist_ok=True)
    for tid, team in teams.items():
        roster = [{**r, "salary": salary_of(r)} for r in roster_all if r["team_id"] == tid]
        injuries = [i for i in d["injuries"] if i["team_id"] == tid]
        pay = payroll_text(payroll[tid], coverage[tid], pay_rank.get(tid), len(complete))
        body = (header(team, d, ranks, d["projection"], pay, injuries)
                + '<section>' + players_card(team, roster, cur, last, abbr_of)
                + lineups_card(tid, seasons, names) + rotation_card(tid, seasons, names)
                + impact_card(roster, impact)
                + '<div class="grid2">' + value_card(roster, impact, salary_of)
                + contracts_card(roster, salary_of, years_of, payroll[tid], pay) + '</div></section>')
        (out_dir / f"{team['abbr']}.html").write_text(page(f"{team['name']} · Luka Watch", "teams", body, d, "../"),
                                                     encoding="utf-8")
    (out_dir / "index.html").write_text(page("Teams · Luka Watch", "teams", index_body(teams, payroll, coverage), d, "../"),
                                        encoding="utf-8")
    return len(teams)


def index_body(teams, payroll, coverage):
    groups = []
    full = lambda t: coverage[t][1] and coverage[t][0] / coverage[t][1] >= PAYROLL_COMPLETE
    for conf in ("West", "East"):
        tiles = "".join(
            f'<a class="team-tile{" me" if t["team_id"] == LAKERS_ID else ""}" href="{t["abbr"]}.html">'
            f'<img src="{t["logo"]}" alt="" class="logo-m"><span class="tt-name">{escape(t["name"])}</span>'
            f'<span class="muted small">{ordinal(t["seed"])} · {t["wins"]}-{t["losses"]} · payroll '
            f'{money(payroll[t["team_id"]])}{"" if full(t["team_id"]) else "+"}</span></a>'
            for t in sorted((t for t in teams.values() if t["conference"] == conf), key=lambda t: t["seed"] or 99))
        groups.append(f'<h2>{conf}ern Conference</h2><div class="team-grid">{tiles}</div>')
    return (f'<section class="hero"><h1>Teams</h1><p class="muted">Every team\'s players, salaries, impact and contracts. '
            f'You can also open a team by clicking it in the standings.</p></section><section>{"".join(groups)}</section>')
