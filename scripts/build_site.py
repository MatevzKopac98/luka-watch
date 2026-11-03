"""Render site/index.html from the latest database snapshot. Run: python scripts/build_site.py"""
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db import connect  # noqa: E402

TZ = ZoneInfo("Europe/Ljubljana")
LAKERS_ID = "13"
LUKA_NAME = "Luka Doncic"
OUT = ROOT / "site" / "index.html"


# ---------- helpers ----------

def local(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ)


def fmt(v, d=1, pct=False):
    if v is None:
        return "–"
    return f"{v:.{d}f}{'%' if pct else ''}"


def rank(rows, key, low_is_good=False):
    """{team_id: rank 1..30} for a stat."""
    vals = sorted((r for r in rows if r[key] is not None), key=lambda r: r[key], reverse=not low_is_good)
    return {r["team_id"]: i for i, r in enumerate(vals, 1)}


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def short_name(name):
    """'Shai Gilgeous-Alexander' -> 'S. Gilgeous-Alexander'"""
    first, _, rest = name.partition(" ")
    return f"{first[0]}. {rest}" if rest else name


def tip(text):
    return f'data-tip="{escape(text, quote=True)}" tabindex="0"'


# ---------- data ----------

def load():
    con = connect()
    d = con.execute("SELECT MAX(snapshot_date) FROM team_season").fetchone()[0]
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]
    data = {
        "date": d,
        "teams": q("SELECT * FROM team_season WHERE snapshot_date=?", d),
        "players": q("""SELECT p.*, s.* FROM player_season s JOIN players p USING (espn_id)
                        WHERE s.snapshot_date=? ORDER BY s.pts DESC""", d),
        "odds": q("SELECT * FROM mvp_odds WHERE snapshot_date=? AND source='Polymarket' ORDER BY prob DESC", d),
        "odds_hist": q("""SELECT snapshot_date, player_name, prob FROM mvp_odds
                          WHERE source='Polymarket' ORDER BY snapshot_date"""),
        "games": q("SELECT * FROM games ORDER BY date_utc"),
        "injuries": q("SELECT * FROM injuries WHERE snapshot_date=?", d),
    }
    luka = next(p for p in data["players"] if p["name"] == LUKA_NAME)
    data["luka"] = luka
    data["luka_games"] = q("SELECT * FROM player_games WHERE espn_id=? ORDER BY game_date", luka["espn_id"])
    con.close()
    return data


def odds_for(name, odds):
    from fetchers.espn import _plain
    return next((o["prob"] for o in odds if _plain(o["player_name"]) == _plain(name)), None)


# ---------- charts (inline SVG) ----------

# Stats selectable on the game-by-game chart: key, button label, season-average column, shooting split
GAME_STATS = [
    ("pts", "Points", "pts", None), ("reb", "Rebounds", "reb", None), ("ast", "Assists", "ast", None),
    ("fg_pct", "FG%", "fg_pct", ("fgm", "fga")), ("fg3_pct", "3PT%", "fg3_pct", ("fg3m", "fg3a")),
    ("ft_pct", "FT%", "ft_pct", ("ftm", "fta")), ("stl", "Steals", "stl", None),
    ("blk", "Blocks", "blk", None), ("tov", "Turnovers", "tov", None), ("min", "Minutes", "min", None),
]


def game_value(g, key, split):
    if split:
        made, att = g[split[0]], g[split[1]]
        return 100 * made / att if att else None
    return g[key]


def nice_max(v):
    """Axis top and gridline step for a count stat."""
    for step in (1, 2, 5, 10):
        if v / step <= 6:
            return step * (int(v / step) + 1), step
    return 10 * (int(v / 10) + 1), 10


def chart_game_stat(games, key, label, avg, split):
    """One stat game by game (last 20), with season average reference line."""
    games = games[-20:]
    W, H, L, R, T, B = 640, 260, 40, 12, 20, 32
    vals = [game_value(g, key, split) for g in games]
    present = [v for v in vals if v is not None]
    if not present:
        return '<p class="muted">No games yet.</p>'
    pct = split is not None
    ymax, step = (100, 20) if pct else nice_max(max(present + [avg or 0]) * 1.1)
    x = lambda i: L + (W - L - R) * (i / max(1, len(games) - 1) if len(games) > 1 else 0.5)
    y = lambda v: T + (H - T - B) * (1 - v / ymax)
    unit = "%" if pct else ""
    grid = "".join(
        f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
        f'<text x="{L - 6}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">{v}{unit}</text>'
        for v in range(0, int(ymax) + 1, step))
    pts = [(x(i), y(v)) for i, v in enumerate(vals) if v is not None]
    path = " ".join(f"{'M' if j == 0 else 'L'}{a:.1f},{b:.1f}" for j, (a, b) in enumerate(pts))
    dots, labels = [], []
    for i, g in enumerate(games):
        d = local(g["game_date"])
        where = "vs" if g["home_away"] == "home" else "@"
        v = vals[i]
        shown = (f"{v:.1f}% ({g[split[0]]:.0f}/{g[split[1]]:.0f})" if pct else f"{v:.0f}") if v is not None \
            else "no attempts"
        t = (f"{d:%a %d %b} {where} {g['opp_abbr']} · {g['result']} {g['score']}|"
             f"{label}: {shown}|"
             f"{g['pts']:.0f} pts · {g['reb']:.0f} reb · {g['ast']:.0f} ast")
        if v is not None:
            dots.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4.5" class="dot-hi pt"/>'
                        f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="18" class="hit" {tip(t)}/>')
        if len(games) <= 10 or (len(games) - 1 - i) % 2 == 0:
            labels.append(f'<text x="{x(i):.1f}" y="{H - 8}" class="tick" text-anchor="middle">{g["opp_abbr"]}</text>')
    best = max((i for i in range(len(vals)) if vals[i] is not None), key=lambda i: vals[i])
    ref = ""
    if avg is not None:
        ref = (f'<line x1="{L}" x2="{W - R}" y1="{y(avg):.1f}" y2="{y(avg):.1f}" class="ref"/>'
               f'<text x="{L + 6}" y="{y(avg) - 6:.1f}" class="ref-label">Season avg {avg:.1f}{unit}</text>')
    hi = (f'<text x="{x(best):.1f}" y="{y(vals[best]) - 10:.1f}" class="val" text-anchor="middle">'
          f'{vals[best]:.0f}{unit}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Luka {label} per game">'
            f'{grid}{ref}<path d="{path}" class="line-hi"/>{"".join(dots)}{hi}{"".join(labels)}</svg>')


def game_stat_switcher(games, season):
    """Buttons above the chart; each shows that stat's chart (all pre-rendered, switched in the browser)."""
    buttons, panels = [], []
    for n, (key, label, avg_key, split) in enumerate(GAME_STATS):
        on = n == 0
        buttons.append(f'<button type="button" class="chip{" on" if on else ""}" data-stat="{key}" '
                       f'aria-pressed="{"true" if on else "false"}">{label}</button>')
        panels.append(f'<div class="stat-panel" data-stat="{key}"{"" if on else " hidden"}>'
                      f'{chart_game_stat(games, key, label, season[avg_key], split)}</div>')
    return (f'<div class="chips" role="group" aria-label="Choose a stat">{"".join(buttons)}</div>'
            f'{"".join(panels)}')


def chart_mvp(odds, luka_name):
    """Horizontal bars of MVP win probability, Luka highlighted."""
    from fetchers.espn import _plain
    top = odds[:8]
    if not top:
        return '<p class="muted">No odds available.</p>'
    W, row, L, R = 340, 30, 140, 36
    H = row * len(top) + 4
    vmax = max(o["prob"] for o in top)
    bars = []
    for i, o in enumerate(top):
        yy = i * row + 4
        w = max(4, (W - L - R) * o["prob"] / vmax)
        cls = "bar-hi" if _plain(o["player_name"]) == _plain(luka_name) else "bar"
        t = f"{o['player_name']}|{o['prob']:.1%} implied chance|bid {o['bid']:.3f} · ask {o['ask']:.3f}"
        bars.append(
            f'<text x="{L - 10}" y="{yy + row / 2 + 1:.1f}" class="lbl" text-anchor="end" dominant-baseline="middle">'
            f'{escape(short_name(o["player_name"]))}</text>'
            f'<rect x="{L}" y="{yy + 6}" width="{w:.1f}" height="{row - 14}" rx="4" class="{cls}"/>'
            f'<text x="{L + w + 8:.1f}" y="{yy + row / 2 + 1:.1f}" class="val" dominant-baseline="middle">'
            f'{o["prob"]:.0%}</text>'
            f'<rect x="0" y="{yy}" width="{W}" height="{row}" class="hit" {tip(t)}/>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="MVP odds">{"".join(bars)}</svg>'


def chart_odds_trend(hist, names):
    """MVP probability over time for the top names (needs 2+ days of snapshots)."""
    from fetchers.espn import _plain
    dates = sorted({h["snapshot_date"] for h in hist})
    if len(dates) < 2:
        return ('<p class="muted note">The odds trend line appears from the second day of data; '
                'every morning adds a new point.</p>')
    W, H, L, R, T, B = 640, 220, 36, 110, 12, 24
    series = {n: {h["snapshot_date"]: h["prob"] for h in hist if _plain(h["player_name"]) == _plain(n)}
              for n in names}
    ymax = max(max(s.values(), default=0) for s in series.values()) * 1.15 or 1
    x = lambda i: L + (W - L - R) * i / (len(dates) - 1)
    y = lambda v: T + (H - T - B) * (1 - v / ymax)
    out = []
    step = 0.1 if ymax > 0.3 else 0.05
    v = 0.0
    while v <= ymax:
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">{v:.0%}</text>')
        v += step
    for k, n in enumerate(names):
        s = series[n]
        pts = [(x(i), y(s[d])) for i, d in enumerate(dates) if d in s]
        if not pts:
            continue
        out.append(f'<path d="{" ".join(("M" if j == 0 else "L") + f"{a:.1f},{b:.1f}" for j, (a, b) in enumerate(pts))}"'
                   f' class="line s{k + 1}"/>')
        out.append(f'<text x="{pts[-1][0] + 8:.1f}" y="{pts[-1][1] + 4:.1f}" class="lbl">{escape(n.split()[-1])}'
                   f' {s[dates[-1]]:.0%}</text>' if dates[-1] in s else "")
    out.append(f'<text x="{L}" y="{H - 4}" class="tick">{dates[0]}</text>'
               f'<text x="{W - R}" y="{H - 4}" class="tick" text-anchor="end">{dates[-1]}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="MVP odds trend">{"".join(out)}</svg>'


def chart_ratings(teams, opp_id):
    """Offensive vs defensive rating for all 30 teams (top-right = best)."""
    W, H, L, R, T, B = 760, 400, 44, 16, 16, 40
    xs = [t["ortg"] for t in teams if t["ortg"]]
    ys = [t["drtg"] for t in teams if t["drtg"]]
    x0, x1 = min(xs) - 1.5, max(xs) + 1.5
    y0, y1 = min(ys) - 1.5, max(ys) + 1.5
    x = lambda v: L + (W - L - R) * (v - x0) / (x1 - x0)
    y = lambda v: T + (H - T - B) * (v - y0) / (y1 - y0)      # low DRtg (good) at top
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    out = [f'<line x1="{x(mx):.1f}" x2="{x(mx):.1f}" y1="{T}" y2="{H - B}" class="ref-soft"/>',
           f'<line x1="{L}" x2="{W - R}" y1="{y(my):.1f}" y2="{y(my):.1f}" class="ref-soft"/>',
           f'<text x="{W - R - 4}" y="{T + 12}" class="quad" text-anchor="end">Good offense · good defense</text>',
           f'<text x="{L + 4}" y="{H - B - 6}" class="quad">Poor offense · poor defense</text>',
           f'<text x="{(L + W - R) / 2:.1f}" y="{H - 8}" class="tick" text-anchor="middle">'
           f'Offensive rating (points per 100 possessions) →</text>',
           f'<text x="12" y="{(T + H - B) / 2:.1f}" class="tick" text-anchor="middle" '
           f'transform="rotate(-90 12 {(T + H - B) / 2:.1f})">← Defensive rating (lower is better)</text>']
    hi = []
    for t in teams:
        if not t["ortg"]:
            continue
        cx, cy = x(t["ortg"]), y(t["drtg"])
        cls = "dot-hi" if t["team_id"] == LAKERS_ID else "dot-opp" if t["team_id"] == opp_id else "dot"
        tt = (f"{t['name']} ({t['wins']}-{t['losses']})|ORtg {t['ortg']} · DRtg {t['drtg']}"
              f"|Net {t['net_rtg']:+.1f}")
        mark = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{6 if cls != "dot" else 4.5}" class="{cls}"/>'
        label = f'<text x="{cx + 10:.1f}" y="{cy + 4:.1f}" class="{"lbl-strong" if cls != "dot" else "tick tlbl"}">{t["abbr"]}</text>'
        hit = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="12" class="hit" {tip(tt)}/>'
        (hi if cls != "dot" else out).append(mark + label + hit)
    return (f'<svg viewBox="0 0 {W} {H}" class="chart scatter" role="img" aria-label="Team ratings">'
            f'{"".join(out)}{"".join(hi)}</svg>')


# ---------- sections ----------

def section_hero(d):
    l = d["luka"]
    prob = odds_for(LUKA_NAME, d["odds"])
    mvp_rank = next((i for i, o in enumerate(d["odds"], 1) if o["player_name"].startswith("Luka")), None)
    last = d["luka_games"][-1] if d["luka_games"] else None
    last5 = d["luka_games"][-5:]
    l5 = sum(g["pts"] for g in last5) / len(last5) if last5 else None
    tiles = [
        ("Points", fmt(l["pts"]), f"Last 5: {fmt(l5)}"),
        ("Rebounds", fmt(l["reb"]), f"{l['gp']} games played"),
        ("Assists", fmt(l["ast"]), f"{fmt(l['tov'])} turnovers"),
        ("True shooting", fmt(l["ts_pct"], pct=True), f"3P {fmt(l['fg3_pct'], pct=True)} on {fmt(l['fg3a'])} att"),
        ("MVP chance", f"{prob:.0%}" if prob else "–", f"{ordinal(mvp_rank)} favourite" if mvp_rank else "Polymarket"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="tile-k">{k}</div><div class="tile-v">{v}</div>'
                         f'<div class="tile-s">{s}</div></div>' for k, v, s in tiles)
    last_html = ""
    if last:
        where = "vs" if last["home_away"] == "home" else "@"
        last_html = (f'<p class="lastgame"><strong>Last game</strong> · {local(last["game_date"]):%a %d %b} {where} '
                     f'{last["opp_abbr"]} · {last["result"]} {last["score"]} — <strong>{last["pts"]:.0f} pts, '
                     f'{last["reb"]:.0f} reb, {last["ast"]:.0f} ast</strong> '
                     f'({last["fgm"]:.0f}/{last["fga"]:.0f} FG, {last["fg3m"]:.0f}/{last["fg3a"]:.0f} 3P)</p>')
    head = f'<img src="{l["headshot"]}" alt="" class="headshot">' if l.get("headshot") else ""
    return f"""
<section class="hero">
  <div class="hero-head">{head}<div><h1>Luka Dončić</h1>
  <p class="muted">Los Angeles Lakers · {l['position'] or ''} · 2026-27 regular season</p></div></div>
  <div class="tiles">{tiles_html}</div>
  {last_html}
  <div class="card"><h3>Game by game · last 20 games</h3>{game_stat_switcher(d['luka_games'], l)}</div>
</section>"""


def section_mvp(d):
    from fetchers.espn import _plain
    top_names = [o["player_name"] for o in d["odds"][:5]]
    return f"""
<section id="mvp">
  <h2>MVP race</h2>
  <p class="muted">Polymarket implied probability (midpoint of bid and ask). Tap a bar for detail.</p>
  <div class="grid2">
    <div class="card"><h3>Chance to win MVP</h3>{chart_mvp(d['odds'], LUKA_NAME)}</div>
    <div class="card"><h3>How the odds move</h3>{chart_odds_trend(d['odds_hist'], top_names)}
      {'' if len({h['snapshot_date'] for h in d['odds_hist']}) < 2 else
       '<div class="legend">' + ''.join(f'<span><i class="sw s{k + 1}"></i>{escape(n)}</span>'
                                       for k, n in enumerate(top_names)) + '</div>'}</div>
  </div>
</section>"""


def section_compare(d):
    players = d["players"]
    cols = [("pts", "PTS", 1, False), ("reb", "REB", 1, False), ("ast", "AST", 1, False),
            ("stl", "STL", 1, False), ("blk", "BLK", 1, False), ("tov", "TOV", 1, True),
            ("fg_pct", "FG%", 1, False), ("fg3_pct", "3P%", 1, False), ("ft_pct", "FT%", 1, False),
            ("ts_pct", "TS%", 1, False), ("min", "MIN", 1, None)]
    best = {}
    for k, _, _, low in cols:
        vals = [p[k] for p in players if p[k] is not None]
        if vals and low is not None:
            best[k] = min(vals) if low else max(vals)
    rows = []
    for p in players:
        prob = odds_for(p["name"], d["odds"])
        cells = "".join(f'<td class="{"best" if best.get(k) == p[k] else ""}">{fmt(p[k], dd)}</td>'
                        for k, _, dd, _ in cols)
        img = f'<img src="{p["headshot"]}" alt="" class="mini">' if p.get("headshot") else ""
        cls = ' class="me"' if p["name"] == LUKA_NAME else ""
        rows.append(f'<tr{cls}><th scope="row">{img}<span>{escape(p["name"])}<small>{p["team_abbr"]} · '
                    f'{p["gp"]} GP</small></span></th><td>{f"{prob:.0%}" if prob else "–"}</td>{cells}'
                    f'<td>{p["dd2"]}</td><td>{p["td3"]}</td></tr>')
    head = "".join(f"<th>{lbl}</th>" for _, lbl, _, _ in cols)
    return f"""
<section id="compare">
  <h2>Luka vs. the best</h2>
  <p class="muted">Per-game averages. The best value in each column is highlighted. TS% = true shooting
  (scoring efficiency including 3s and free throws). The group is the core five plus the current MVP top 5.</p>
  <div class="tablewrap"><table class="stats">
    <thead><tr><th scope="col">Player</th><th>MVP</th>{head}<th>DD</th><th>TD</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div>
</section>"""


def team_block_rows(teams):
    """Stat definitions with ranks across the league."""
    defs = [("ppg", "Points scored", False, 1), ("opp_ppg", "Points allowed", True, 1),
            ("ortg", "Offensive rating", False, 1), ("drtg", "Defensive rating", True, 1),
            ("net_rtg", "Net rating", False, 1), ("pace", "Pace (possessions)", None, 1),
            ("fg_pct", "Field goal %", False, 1), ("fg3_pct", "3-point %", False, 1),
            ("reb", "Rebounds", False, 1), ("ast", "Assists", False, 1), ("tov", "Turnovers", True, 1),
            ("stl", "Steals", False, 1), ("blk", "Blocks", False, 1)]
    ranks = {k: rank(teams, k, low) for k, _, low, _ in defs if low is not None}
    return defs, ranks


def section_lakers(d):
    teams = d["teams"]
    lal = next(t for t in teams if t["team_id"] == LAKERS_ID)
    defs, ranks = team_block_rows(teams)
    west = sorted((t for t in teams if t["conference"] == lal["conference"]), key=lambda t: t["seed"] or 99)
    finals = [g for g in d["games"] if g["status"] == "STATUS_FINAL"][-5:]
    upcoming = [g for g in d["games"] if g["status"] != "STATUS_FINAL"]
    opp_id = None
    if upcoming:
        g = upcoming[0]
        opp_id = g["away_id"] if g["home_id"] == LAKERS_ID else g["home_id"]

    tiles = [("Record", f"{lal['wins']}-{lal['losses']}", f"{ordinal(lal['seed'])} in the West"),
             ("Streak", lal["streak"], f"Last 10: {lal['last10']}"),
             ("Home / Road", f"{lal['home']} · {lal['road']}", "home · road"),
             ("Net rating", f"{lal['net_rtg']:+.1f}", f"{ordinal(ranks['net_rtg'][LAKERS_ID])} in NBA")]
    tiles_html = "".join(f'<div class="tile"><div class="tile-k">{k}</div><div class="tile-v">{v}</div>'
                         f'<div class="tile-s">{s}</div></div>' for k, v, s in tiles)

    def rank_bar(r):
        pct = (31 - r) / 30 * 100
        return f'<span class="rbar"><i style="width:{pct:.0f}%"></i></span>'

    stat_rows = "".join(
        f'<tr><th scope="row">{lbl}</th><td>{fmt(lal[k])}</td>'
        f'<td>{ordinal(ranks[k][LAKERS_ID]) if k in ranks else "–"}</td>'
        f'<td>{rank_bar(ranks[k][LAKERS_ID]) if k in ranks else ""}</td></tr>'
        for k, lbl, _, _ in defs)

    res = []
    for g in reversed(finals):
        home = g["home_id"] == LAKERS_ID
        us, them = (g["home_score"], g["away_score"]) if home else (g["away_score"], g["home_score"])
        opp = g["away_abbr"] if home else g["home_abbr"]
        wl = "W" if us > them else "L"
        res.append(f'<li><span class="wl {wl}">{wl}</span> {local(g["date_utc"]):%d %b} '
                   f'{"vs" if home else "@"} {opp} <strong>{us}-{them}</strong></li>')

    stand = "".join(
        f'<tr class="{"me" if t["team_id"] == LAKERS_ID else "opp" if t["team_id"] == opp_id else ""}'
        f'{" cut" if t["seed"] in (6, 10) else ""}">'
        f'<td>{t["seed"]}</td><th scope="row"><img src="{t["logo"]}" alt="" class="logo">{t["abbr"]}</th>'
        f'<td>{t["wins"]}-{t["losses"]}</td><td>{t["games_behind"]}</td><td>{t["streak"]}</td>'
        f'<td>{t["last10"]}</td><td>{t["net_rtg"]:+.1f}</td></tr>' for t in west)

    return f"""
<section id="lakers">
  <h2>Lakers</h2>
  <div class="tiles">{tiles_html}</div>
  <div class="grid2">
    <div class="card"><h3>Team stats & league rank</h3>
      <table class="kv"><thead><tr><th></th><th>Per game</th><th>Rank</th><th></th></tr></thead>
      <tbody>{stat_rows}</tbody></table>
      <p class="muted small">Ratings are per 100 possessions, estimated from box-score totals.
      Rank is out of 30 (1st = best).</p></div>
    <div class="card"><h3>Last 5 games</h3><ul class="results">{''.join(res)}</ul>
      <h3 style="margin-top:20px">Western Conference</h3>
      <div class="tablewrap"><table class="stand"><thead><tr><th>#</th><th>Team</th><th>W-L</th><th>GB</th>
      <th>Strk</th><th>L10</th><th>Net</th></tr></thead><tbody>{stand}</tbody></table></div>
      <p class="muted small">Lines mark the playoff (1–6) and play-in (7–10) spots.</p></div>
  </div>
  <div class="card"><h3>Lakers vs. the league: offense and defense</h3>
    <div class="legend"><span><i class="sw hi"></i>Lakers</span><span><i class="sw opp"></i>Next opponent</span>
    <span><i class="sw base"></i>Other teams</span></div>
    {chart_ratings(teams, opp_id)}</div>
</section>"""


def section_next(d):
    teams = {t["team_id"]: t for t in d["teams"]}
    upcoming = [g for g in d["games"] if g["status"] != "STATUS_FINAL"]
    if not upcoming:
        return ""
    g = upcoming[0]
    home = g["home_id"] == LAKERS_ID
    opp_id = g["away_id"] if home else g["home_id"]
    lal, opp = teams[LAKERS_ID], teams[opp_id]
    defs, ranks = team_block_rows(d["teams"])
    when = local(g["date_utc"])

    rows = []
    for k, lbl, low, dd in defs:
        a, b = lal[k], opp[k]
        better = None
        if low is not None and a is not None and b is not None and a != b:
            better = "a" if (a < b) == low else "b"
        ra = f'<small>{ordinal(ranks[k][LAKERS_ID])}</small>' if k in ranks else ""
        rb = f'<small>{ordinal(ranks[k][opp_id])}</small>' if k in ranks else ""
        rows.append(f'<tr><td class="{"edge" if better == "a" else ""}">{fmt(a, dd)} {ra}</td>'
                    f'<th scope="row">{lbl}</th><td class="{"edge" if better == "b" else ""}">{fmt(b, dd)} {rb}</td></tr>')

    h2h = [x for x in d["games"] if x["status"] == "STATUS_FINAL" and opp_id in (x["home_id"], x["away_id"])]
    h2h_html = "".join(
        f'<li>{local(x["date_utc"]):%d %b %Y}: {x["away_abbr"]} {x["away_score"]} @ {x["home_abbr"]} '
        f'{x["home_score"]}</li>' for x in h2h) or "<li class='muted'>First meeting this season</li>"

    def inj(tid):
        items = [i for i in d["injuries"] if i["team_id"] == tid]
        if not items:
            return "<li class='muted'>No reported injuries</li>"
        return "".join(f'<li><strong>{escape(i["player"] or "")}</strong> · {escape(i["status"] or "")}'
                       f'{" — " + escape(i["detail"]) if i["detail"] else ""}</li>' for i in items)

    return f"""
<section id="next">
  <h2>Next game</h2>
  <div class="card matchup">
    <div class="mu-head">
      <div class="mu-team"><img src="{lal['logo']}" alt="" class="biglogo"><strong>Lakers</strong>
        <span class="muted">{lal['wins']}-{lal['losses']} · {ordinal(lal['seed'])} West</span></div>
      <div class="mu-mid"><div class="mu-at">{'vs' if home else '@'}</div>
        <div class="mu-when">{when:%A %d %B}</div><div class="mu-time">{when:%H:%M} Ljubljana</div></div>
      <div class="mu-team"><img src="{opp['logo']}" alt="" class="biglogo"><strong>{escape(opp['name'].split()[-1])}</strong>
        <span class="muted">{opp['wins']}-{opp['losses']} · {ordinal(opp['seed'])} {opp['conference']} · {opp['streak']}</span></div>
    </div>
    <table class="vs"><tbody>{''.join(rows)}</tbody></table>
    <p class="muted small">Small number = league rank out of 30. The team with the edge on each stat is highlighted.</p>
  </div>
  <div class="grid2">
    <div class="card"><h3>Injuries · Lakers</h3><ul class="inj">{inj(LAKERS_ID)}</ul></div>
    <div class="card"><h3>Injuries · {escape(opp['name'])}</h3><ul class="inj">{inj(opp_id)}</ul></div>
  </div>
  <div class="card"><h3>Head-to-head this season</h3><ul class="results">{h2h_html}</ul></div>
</section>"""


# ---------- page ----------

CSS = """
:root{color-scheme:light;--page:#f6f5f2;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--hi:#6250d6;--hi-wash:rgba(98,80,214,.08);
--opp:#eb6834;--base:#b9b8b0;--edge:rgba(98,80,214,.12);--good:#006300;--bad:#d03b3b;
--s1:#6250d6;--s2:#eb6834;--s3:#1baf7a;--s4:#2a78d6;--s5:#e87ba4}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,.10);--hi:#9085e9;--hi-wash:rgba(144,133,233,.12);--opp:#d95926;--base:#5a5955;
--edge:rgba(144,133,233,.18);--good:#0ca30c;--s1:#9085e9;--s2:#d95926;--s3:#199e70;--s4:#3987e5;--s5:#d55181}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--hi:#9085e9;--hi-wash:rgba(144,133,233,.12);
--opp:#d95926;--base:#5a5955;--edge:rgba(144,133,233,.18);--good:#0ca30c;--s1:#9085e9;--s2:#d95926;
--s3:#199e70;--s4:#3987e5;--s5:#d55181}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header.top{position:sticky;top:0;z-index:5;background:var(--page);border-bottom:1px solid var(--border)}
.bar-in{max-width:1080px;margin:0 auto;padding:10px 16px;display:flex;gap:16px;align-items:center;overflow-x:auto}
.brand{font-weight:700;white-space:nowrap}
nav a{color:var(--ink2);text-decoration:none;white-space:nowrap;font-size:14px}nav a:hover{color:var(--ink)}
nav{display:flex;gap:14px}
.updated{margin-left:auto;color:var(--muted);font-size:12px;white-space:nowrap}
main{max-width:1080px;margin:0 auto;padding:16px 16px 48px}
section{margin-top:36px}section.hero{margin-top:12px}
h1{font-size:30px;margin:0;letter-spacing:-.01em}h2{font-size:22px;margin:0 0 4px}h3{font-size:15px;margin:0 0 10px}
.muted{color:var(--muted)}.small{font-size:12px}.note{margin:24px 0}
.hero-head{display:flex;gap:16px;align-items:center}
.headshot{width:84px;height:84px;border-radius:50%;object-fit:cover;background:var(--surface);border:1px solid var(--border)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.tile-k{font-size:12px;color:var(--ink2);text-transform:uppercase;letter-spacing:.04em}
.tile-v{font-size:28px;font-weight:700;margin:2px 0}.tile-s{font-size:12px;color:var(--muted)}
.lastgame{background:var(--hi-wash);border-radius:10px;padding:10px 14px;margin:0 0 14px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:12px;min-width:0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:760px){.grid2{grid-template-columns:1fr}h1{font-size:24px}.tile-v{font-size:22px}}
.chart{width:100%;height:auto;display:block;overflow:visible}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .tick{fill:var(--muted);font-size:11px}
.chart .lbl{fill:var(--ink2);font-size:13px}.chart .lbl-strong{fill:var(--ink);font-size:12px;font-weight:700}
.chart .val{fill:var(--ink);font-size:12px;font-weight:600}
.chart .ref{stroke:var(--ink2);stroke-dasharray:4 4;stroke-width:1}.chart .ref-label{fill:var(--ink2);font-size:11px}
.chart .ref-soft{stroke:var(--axis);stroke-dasharray:3 4}.chart .quad{fill:var(--muted);font-size:11px;font-style:italic}
.chart .line-hi{fill:none;stroke:var(--hi);stroke-width:2;stroke-linejoin:round}
.chart .line{fill:none;stroke-width:2}
.chart .s1{stroke:var(--s1)}.chart .s2{stroke:var(--s2)}.chart .s3{stroke:var(--s3)}.chart .s4{stroke:var(--s4)}.chart .s5{stroke:var(--s5)}
.chart .dot-hi{fill:var(--hi);stroke:var(--surface);stroke-width:2}
.chart .dot-opp{fill:var(--opp);stroke:var(--surface);stroke-width:2}
.chart .dot{fill:var(--base);stroke:var(--surface);stroke-width:1.5}
.chart .bar{fill:var(--base)}.chart .bar-hi{fill:var(--hi)}
.chart .hit{fill:transparent;cursor:pointer;outline:none}
.chart .hit:hover,.chart .hit:focus{fill:var(--hi-wash)}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink2);margin:0 0 8px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:10px;height:10px;border-radius:50%;display:inline-block}
.sw.hi{background:var(--hi)}.sw.opp{background:var(--opp)}.sw.base{background:var(--base)}
.sw.s1{background:var(--s1)}.sw.s2{background:var(--s2)}.sw.s3{background:var(--s3)}.sw.s4{background:var(--s4)}.sw.s5{background:var(--s5)}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:12px;border-radius:12px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:7px 8px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--grid)}
thead th{font-size:12px;color:var(--ink2);font-weight:600}
table.stats{background:var(--surface);border:1px solid var(--border)}
table.stats th[scope=row]{text-align:left;display:flex;align-items:center;gap:8px;font-weight:600;position:sticky;left:0;background:var(--surface)}
table.stats th[scope=row] small{display:block;font-weight:400;color:var(--muted);font-size:11px}
table.stats thead th:first-child{text-align:left;position:sticky;left:0;background:var(--surface)}
table.stats tr.me th,table.stats tr.me td{background:var(--hi-wash)}
td.best{font-weight:700;box-shadow:inset 0 -2px 0 var(--hi)}
.mini{width:30px;height:30px;border-radius:50%;object-fit:cover;background:var(--page)}
table.kv th[scope=row]{text-align:left;font-weight:500;color:var(--ink2)}
.rbar{display:inline-block;width:70px;height:6px;border-radius:3px;background:var(--grid);vertical-align:middle}
.rbar i{display:block;height:100%;border-radius:3px;background:var(--hi)}
ul.results,ul.inj{list-style:none;margin:0;padding:0}ul.results li,ul.inj li{padding:6px 0;border-bottom:1px solid var(--grid)}
ul.inj li{font-size:14px;color:var(--ink2)}ul.inj strong{color:var(--ink)}
.wl{display:inline-block;width:22px;text-align:center;border-radius:4px;font-weight:700;font-size:12px;margin-right:6px}
.wl.W{color:var(--good);background:rgba(12,163,12,.12)}.wl.L{color:var(--bad);background:rgba(208,59,59,.12)}
table.stand th[scope=row]{text-align:left;display:flex;align-items:center;gap:6px}
table.stand tr.me td,table.stand tr.me th{background:var(--hi-wash);font-weight:700}
table.stand tr.opp th{box-shadow:inset 3px 0 0 var(--opp)}
table.stand tr.cut td,table.stand tr.cut th{border-bottom:2px solid var(--axis)}
.logo{width:20px;height:20px}.biglogo{width:64px;height:64px}
.mu-head{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:center;text-align:center;margin-bottom:12px}
.mu-team{display:flex;flex-direction:column;align-items:center;gap:2px}.mu-team strong{font-size:18px}
.mu-team .muted{font-size:12px}.mu-at{font-size:13px;color:var(--muted)}.mu-when{font-weight:700}.mu-time{font-size:22px;font-weight:700}
table.vs td{width:38%;text-align:center}table.vs th{text-align:center;font-weight:500;color:var(--ink2);font-size:13px}
table.vs td small{color:var(--muted);font-size:11px;margin-left:4px}
table.vs td.edge{background:var(--edge);font-weight:700}
@media (max-width:760px){
.bar-in{flex-wrap:wrap;row-gap:2px;overflow:visible}nav{order:3;width:100%;overflow-x:auto;padding-bottom:2px}
table.kv th,table.kv td{padding:7px 4px}.rbar{width:44px}
.chart .tick{font-size:19px}.chart .ref-label,.chart .val{font-size:19px}.chart .pt{r:7}
.chart .lbl{font-size:12px}
.scatter .tick{font-size:24px}.scatter .tlbl{display:none}.scatter .lbl-strong{font-size:28px}
.scatter .quad{font-size:22px}.scatter .dot{r:9}.scatter .dot-hi,.scatter .dot-opp{r:13}}
.chips{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px;margin:0 0 10px;-webkit-overflow-scrolling:touch}
.chip{flex:none;font:inherit;font-size:13px;padding:6px 12px;border-radius:999px;cursor:pointer;
border:1px solid var(--border);background:var(--page);color:var(--ink2)}
.chip:hover{color:var(--ink)}.chip.on{background:var(--hi);border-color:var(--hi);color:var(--surface);font-weight:600}
.chip:focus-visible{outline:2px solid var(--hi);outline-offset:2px}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);padding:8px 10px;border-radius:8px;
font-size:12px;line-height:1.45;max-width:260px;opacity:0;transition:opacity .1s;z-index:10}
#tip b{font-weight:700}
footer{max-width:1080px;margin:0 auto;padding:0 16px 32px;color:var(--muted);font-size:12px}
"""

JS = """
const tip=document.getElementById('tip');
function show(el,x,y){const p=el.dataset.tip.split('|');tip.innerHTML='<b>'+p[0]+'</b>'+(p.length>1?'<br>'+p.slice(1).join('<br>'):'');
tip.style.opacity=1;const r=tip.getBoundingClientRect();let L=x+14,T=y+14;
if(L+r.width>innerWidth-8)L=x-r.width-14;if(T+r.height>innerHeight-8)T=y-r.height-14;tip.style.left=Math.max(8,L)+'px';tip.style.top=Math.max(8,T)+'px'}
document.querySelectorAll('[data-tip]').forEach(el=>{
el.addEventListener('pointermove',e=>show(el,e.clientX,e.clientY));
el.addEventListener('pointerleave',()=>tip.style.opacity=0);
el.addEventListener('focus',()=>{const b=el.getBoundingClientRect();show(el,b.right,b.top)});
el.addEventListener('blur',()=>tip.style.opacity=0)});
document.querySelectorAll('.chips').forEach(group=>{const card=group.parentElement;
group.addEventListener('click',e=>{const b=e.target.closest('.chip');if(!b)return;
group.querySelectorAll('.chip').forEach(c=>{const on=c===b;c.classList.toggle('on',on);c.setAttribute('aria-pressed',on)});
card.querySelectorAll('.stat-panel').forEach(p=>p.hidden=p.dataset.stat!==b.dataset.stat);tip.style.opacity=0})});
"""


def build():
    d = load()
    now = datetime.now(TZ)
    body = section_hero(d) + section_next(d) + section_mvp(d) + section_compare(d) + section_lakers(d)
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Luka Watch</title><meta name="description" content="Daily Luka Dončić, MVP race and Lakers tracker">
<style>{CSS}</style></head><body>
<header class="top"><div class="bar-in"><span class="brand">Luka Watch</span>
<nav><a href="#next">Next game</a><a href="#mvp">MVP race</a><a href="#compare">Compare</a><a href="#lakers">Lakers</a></nav>
<span class="updated">Updated {now:%a %d %b, %H:%M}</span></div></header>
<main>{body}</main>
<footer>Data: ESPN (stats, standings, schedule, injuries) and Polymarket (MVP odds). Times in Ljubljana time.
Ratings are estimates from box-score totals. Snapshot {d['date']}.</footer>
<div id="tip" role="tooltip"></div><script>{JS}</script></body></html>"""
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
