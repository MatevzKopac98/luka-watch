"""Render site/index.html from the latest database snapshot. Run: python scripts/build_site.py"""
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db import connect  # noqa: E402
from scripts import insights  # noqa: E402

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
    data["league"] = q("""SELECT * FROM league_players
                          WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM league_players)""")
    data["luka_games"] = q("SELECT * FROM player_games WHERE espn_id=? ORDER BY game_date", luka["espn_id"])

    # Finished games with box scores
    data["game_info"] = q("SELECT * FROM game_info WHERE status='STATUS_FINAL' ORDER BY start_utc")
    data["box"], data["box_players"] = {}, {}
    for r in q("SELECT * FROM game_team_box"):
        data["box"].setdefault(r["event_id"], {})[r["team_id"]] = r
    for r in q("SELECT * FROM game_players"):
        data["box_players"].setdefault(r["event_id"], []).append(r)
    lal = [g for g in data["game_info"] if LAKERS_ID in (g["home_id"], g["away_id"])]
    data["lakers_last"] = lal[-1] if lal else None
    data["last_night"] = max((g["us_date"] for g in data["game_info"]), default=None)
    career = q("SELECT * FROM player_career WHERE espn_id=? ORDER BY snapshot_date DESC LIMIT 1", luka["espn_id"])
    data["career"] = career[0] if career else None
    data["news"] = q("SELECT * FROM luka_news ORDER BY published DESC LIMIT 3")
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
        conf = "east" if t["conference"] == "East" else "west"
        cls = "dot-hi" if t["team_id"] == LAKERS_ID else "dot-opp" if t["team_id"] == opp_id else f"dot {conf}"
        tt = (f"{t['name']} ({t['wins']}-{t['losses']})|ORtg {t['ortg']} · DRtg {t['drtg']}"
              f"|Net {t['net_rtg']:+.1f}")
        base = cls.startswith("dot ")
        mark = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{4.5 if base else 6}" class="{cls}"/>'
        label = f'<text x="{cx + 10:.1f}" y="{cy + 4:.1f}" class="{"tick tlbl" if base else "lbl-strong"}">{t["abbr"]}</text>'
        hit = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="12" class="hit" {tip(tt)}/>'
        (out if base else hi).append(f'<g class="conf-{conf}{"" if base else " pinned"}">{mark}{label}{hit}</g>')
    return (f'<svg viewBox="0 0 {W} {H}" class="chart scatter" role="img" aria-label="Team ratings">'
            f'{"".join(out)}{"".join(hi)}</svg>')


# ---------- sections ----------

def highlights_url(game, teams):
    """YouTube search for Luka's highlights from that game (US date, opponent nickname)."""
    names = {t["abbr"]: t["name"] for t in teams}
    opp = names.get(game["opp_abbr"], game["opp_abbr"]).split()[-1]
    us = datetime.fromisoformat(game["game_date"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
    q = f"Luka Doncic highlights vs {opp} {us:%B} {us.day}, {us.year}"
    return "https://www.youtube.com/results?search_query=" + quote_plus(q)


MIN_GAMES = 65          # NBA award eligibility (MVP, All-NBA)


def eligibility_tile(d):
    """65-game rule: a game counts with 20+ minutes; up to two games of 15-20 minutes also count."""
    mins = [g["min"] or 0 for g in d["luka_games"]]
    counted = sum(m >= 20 for m in mins) + min(2, sum(15 <= m < 20 for m in mins))
    team_played = sum(1 for g in d["games"] if g["status"] == "STATUS_FINAL")
    can_miss = (82 - MIN_GAMES) - (team_played - counted)
    warn = " warn" if can_miss <= 5 else ""
    return ("Award eligibility", f"{counted} / {MIN_GAMES}",
            f'<span class="{warn.strip()}">can miss {max(can_miss, 0)} more</span>' if can_miss >= 0
            else '<span class="warn">no longer eligible</span>')


def luka_streaks(games):
    pts = [g["pts"] or 0 for g in games]
    cur = 0
    for p in reversed(pts):
        if p < 30:
            break
        cur += 1
    best = run = 0
    for p in pts:
        run = run + 1 if p >= 30 else 0
        best = max(best, run)
    hi = lambda k: max(games, key=lambda g: g[k] or 0) if games else None
    return pts, cur, best, hi


def season_bests(d, luka_id):
    """League-wide single-game bests this season, from every stored box score."""
    rows = [p for ps in d["box_players"].values() for p in ps]
    out = []
    for k, lbl in (("pts", "Points"), ("ast", "Assists"), ("reb", "Rebounds"), ("fg3m", "3-pointers")):
        if not rows:
            break
        top = max(rows, key=lambda p: p[k] or 0)
        mine = [p for p in rows if p["athlete_id"] == luka_id]
        best_mine = max(mine, key=lambda p: p[k] or 0) if mine else None
        rank = 1 + sum(1 for p in rows if (p[k] or 0) > (best_mine[k] or 0)) if best_mine else None
        out.append((k, lbl, top, best_mine, rank))
    return out


def section_milestones(d):
    l, games, c = d["luka"], d["luka_games"], d["career"]
    if not games:
        return ""
    pts, cur, best, hi = luka_streaks(games)
    hp, ha, hr = hi("pts"), hi("ast"), hi("reb")
    vs = lambda g: f'{"vs" if g["home_away"] == "home" else "@"} {g["opp_abbr"]}'
    streak_items = [
        (sum(p >= 30 for p in pts), "30-point games", f"of {len(pts)} played"),
        (sum(p >= 40 for p in pts), "40-point games", f"{sum(p >= 50 for p in pts)} of them 50+"),
        (cur, "Current 30+ streak", f"season best {best}"),
        (f'{hp["pts"]:.0f}', "Season-high points", vs(hp)),
        (f'{ha["ast"]:.0f}', "Season-high assists", vs(ha)),
        (f'{hr["reb"]:.0f}', "Season-high rebounds", vs(hr)),
    ]
    streaks = "".join(f'<div class="ms-item"><div class="ms-v">{v}</div><div class="ms-k">{k}</div>'
                      f'<div class="ms-s">{s}</div></div>' for v, k, s in streak_items)

    miles = ""
    if c:
        defs = [("pts", "Points", 1000, l["pts"]), ("ast", "Assists", 500, l["ast"]),
                ("reb", "Rebounds", 500, l["reb"]), ("fg3m", "3-pointers", 100, l["fg3m"]),
                ("td3", "Triple-doubles", 10, None)]
        for k, lbl, step, pace in defs:
            have = c[k] or 0
            target = (int(have // step) + 1) * step
            left = target - have
            prog = 100 * (have - (target - step)) / step
            eta = f" · ~{left / pace:.0f} games at his pace" if pace else ""
            miles += (f'<div class="mile"><div class="mile-top"><span>{lbl}</span><strong>{have:,.0f}</strong></div>'
                      f'<span class="rbar wide"><i style="width:{prog:.0f}%"></i></span>'
                      f'<div class="ms-s">{left:,.0f} to {target:,}{eta}</div></div>')

    bests = ""
    for k, lbl, top, mine, rank in season_bests(d, l["espn_id"]):
        leads = bool(mine) and (mine[k] or 0) >= (top[k] or 0)
        who = "Luka ★" if leads else escape(short_name(top["name"]))
        luka_bit = "" if leads or not mine else f' · Luka {mine[k]:.0f} ({ordinal(rank)})'
        bests += (f'<li class="{"lead" if leads else ""}"><span>{lbl}</span>'
                  f'<strong>{top[k]:.0f}</strong> {who}<span class="muted small">{luka_bit}</span></li>')

    news = "".join(f'<li><a href="{escape(n["url"])}" target="_blank" rel="noopener">{escape(n["headline"])}</a>'
                   f' <span class="muted small">{n["published"][:10]}</span></li>' for n in d["news"])
    records = f'<div class="records"><span class="lbl-k">In the news</span><ul>{news}</ul></div>' if news else ""
    return f"""
<section id="milestones">
  <h2>Milestones & streaks</h2>
  <div class="card ms">
    <div class="ms-grid">
      <div><h3>This season</h3><div class="ms-items">{streaks}</div></div>
      <div><h3>Career milestones</h3>{miles or '<p class="muted">Career totals unavailable.</p>'}
        <p class="muted small">Regular season, {c['gp']:.0f} career games.</p></div>
      <div><h3>NBA single-game bests this season</h3><ul class="bests">{bests}</ul>
        <p class="muted small">★ = Luka holds the league's best mark this season.</p></div>
    </div>
    {records}
  </div>
</section>"""


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
        ("Field goal %", fmt(l["fg_pct"], pct=True), f"{fmt(l['fgm'])} of {fmt(l['fga'])} per game"),
        ("True shooting", fmt(l["ts_pct"], pct=True), f"3P {fmt(l['fg3_pct'], pct=True)} on {fmt(l['fg3a'])} att"),
        ("MVP chance", f"{prob:.0%}" if prob else "–", f"{ordinal(mvp_rank)} favourite" if mvp_rank else "Polymarket"),
        eligibility_tile(d),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="tile-k">{k}</div><div class="tile-v">{v}</div>'
                         f'<div class="tile-s">{s}</div></div>' for k, v, s in tiles)
    last_html = ""
    if last:
        where = "vs" if last["home_away"] == "home" else "@"
        info = next((g for g in d["game_info"] if g["event_id"] == last["event_id"]), {})
        last_html = (f'<div class="lastgame"><p><strong>Last game</strong> · {local(last["game_date"]):%a %d %b} '
                     f'{where} {last["opp_abbr"]} · {last["result"]} {last["score"]} — <strong>{last["pts"]:.0f} pts, '
                     f'{last["reb"]:.0f} reb, {last["ast"]:.0f} ast</strong> '
                     f'({last["fgm"]:.0f}/{last["fga"]:.0f} FG, {last["fg3m"]:.0f}/{last["fg3a"]:.0f} 3P)</p>'
                     f'{highlight_buttons(info, d["teams"], highlights_url(last, d["teams"]))}</div>')
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
    # MVP favourites first; players without a market price go last, by scoring
    players = sorted(d["players"], key=lambda p: (-(odds_for(p["name"], d["odds"]) or 0), -(p["pts"] or 0)))
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
  <p class="muted">Per-game averages, sorted by MVP odds. The best value in each column is highlighted. TS% = true shooting
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


def rank_bar(r, n):
    pct = (n + 1 - r) / n * 100
    return f'<span class="rbar"><i style="width:{pct:.0f}%"></i></span>'


# Luka vs. every qualified player: key, label, lower-is-better, (volume column, minimum) for percentages
LUKA_RANKS = [
    ("pts", "Points", False, None), ("reb", "Rebounds", False, None), ("ast", "Assists", False, None),
    ("stl", "Steals", False, None), ("blk", "Blocks", False, None), ("tov", "Turnovers", True, None),
    ("min", "Minutes", False, None), ("fg_pct", "Field goal %", False, ("fga", 5)),
    ("fg3m", "3-pointers made", False, None), ("fg3_pct", "3-point %", False, ("fg3a", 3)),
    ("ft_pct", "Free throw %", False, ("fta", 2)), ("ts_pct", "True shooting %", False, ("fga", 8)),
    ("dd2", "Double-doubles", False, None), ("td3", "Triple-doubles", False, None),
]


def luka_rank_table(d):
    league = d["league"]
    me = next((p for p in league if p["name"] == LUKA_NAME), None)
    if not me:
        return '<p class="muted">Luka is not in the qualified-player list yet.</p>'
    rows = []
    for k, lbl, low, vol in LUKA_RANKS:
        pool = [p for p in league if p[k] is not None and (not vol or (p[vol[0]] or 0) >= vol[1])]
        v = me[k]
        if v is None or me not in pool or (k in ("dd2", "td3") and not v):
            rows.append(f'<tr><th scope="row">{lbl}</th><td>{fmt(v)}</td><td>–</td><td></td></tr>')
            continue
        r = 1 + sum(1 for p in pool if (p[k] < v if low else p[k] > v))
        pct = k.endswith("pct")
        rows.append(f'<tr><th scope="row">{lbl}</th><td>{fmt(v, 0 if k in ("dd2", "td3") else 1, pct)}</td>'
                    f'<td>{ordinal(r)} <small class="muted">/ {len(pool)}</small></td>'
                    f'<td>{rank_bar(r, len(pool))}</td></tr>')
    return (f'<table class="kv"><thead><tr><th></th><th>Per game</th><th>NBA rank</th><th></th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
            f'<p class="muted small">Ranked against all {len(league)} qualified players. Shooting percentages only count '
            f'players with real volume (FG% 5+ attempts a game, 3P% 3+, FT% 2+, TS% 8+ shots).</p>')


SCHEDULE_AHEAD = 7


def schedule_card(d):
    """Next games with opponent strength, home/away, rest and back-to-backs."""
    teams = {t["team_id"]: t for t in d["teams"]}
    net_rank = rank(d["teams"], "net_rtg")
    sched = d["games"]
    up = [g for g in sched if g["status"] != "STATUS_FINAL"][:SCHEDULE_AHEAD]
    if not up:
        return '<div class="card"><h3>Coming up</h3><p class="muted">No games scheduled.</p></div>'
    us_day = lambda g: datetime.fromisoformat(g["date_utc"].replace("Z", "+00:00")).astimezone(
        ZoneInfo("America/New_York")).date()
    prev = [g for g in sched if g["status"] == "STATUS_FINAL"]
    last_day = us_day(prev[-1]) if prev else None
    rows, nets, b2b, road = [], [], 0, 0
    for g in up:
        home = g["home_id"] == LAKERS_ID
        opp = teams.get(g["away_id"] if home else g["home_id"], {})
        day = us_day(g)
        rest = (day - last_day).days - 1 if last_day else None
        last_day = day
        is_b2b = rest == 0
        b2b += is_b2b
        road += not home
        net = opp.get("net_rtg")
        if net is not None:
            nets.append(net)
        level = "hard" if (net or 0) >= 4 else "easy" if (net or 0) <= -4 else "mid"
        rows.append(
            f'<tr><td>{local(g["date_utc"]):%a %d %b}</td><td class="muted">{"vs" if home else "@"}</td>'
            f'<th scope="row"><img src="{opp.get("logo", "")}" alt="" class="logo">{opp.get("abbr", "")}</th>'
            f'<td>{opp.get("wins", "")}-{opp.get("losses", "")}</td>'
            f'<td><span class="lvl {level}">{fmt(net)}</span> <small class="muted">'
            f'{ordinal(net_rank[opp["team_id"]]) if opp.get("team_id") in net_rank else ""}</small></td>'
            f'<td>{"<span class=\"tag b2b\">B2B</span>" if is_b2b else (f"{rest}d rest" if rest is not None else "")}'
            f'</td></tr>')
    avg = sum(nets) / len(nets) if nets else 0
    verdict = "Tough stretch" if avg >= 2.5 else "Soft stretch" if avg <= -2.5 else "Average stretch"
    return f"""
    <div class="card"><h3>Coming up · next {len(up)} games</h3>
      <p class="verdict"><strong>{verdict}</strong> · opponents' average net rating {avg:+.1f} ·
      {road} on the road · {b2b} back-to-back{'s' if b2b != 1 else ''}</p>
      <div class="tablewrap"><table class="sched"><thead><tr><th>Date</th><th></th><th>Opp</th><th>W-L</th>
      <th>Net rtg</th><th>Rest</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
      <p class="muted small">Net rating = points scored minus allowed per 100 possessions (rank of 30).
      B2B = second night of a back-to-back, when stars most often rest.</p></div>"""


IMPACT_TOP = 3


def impact_card(d):
    """Lakers players with the best plus/minus per game this season (regular rotation only)."""
    games = [g["event_id"] for g in d["game_info"] if LAKERS_ID in (g["home_id"], g["away_id"])]
    agg = {}
    for eid in games:
        for p in d["box_players"].get(eid, []):
            if p["team_id"] != LAKERS_ID or not p.get("min"):
                continue
            a = agg.setdefault(p["athlete_id"], {"name": p["name"], "gp": 0, "min": 0, "pm": 0,
                                                 "pts": 0, "reb": 0, "ast": 0})
            a["gp"] += 1
            for k, src in (("min", "min"), ("pm", "plus_minus"), ("pts", "pts"), ("reb", "reb"), ("ast", "ast")):
                a[k] += p.get(src) or 0
    pool = [a for a in agg.values() if a["gp"] >= max(3, 0.4 * len(games)) and a["min"] / a["gp"] >= 15]
    top = sorted(pool, key=lambda a: -a["pm"] / a["gp"])[:IMPACT_TOP]
    items = "".join(
        f'<li><span class="imp-rank">{i}</span><div class="imp-main"><strong>{escape(a["name"])}</strong>'
        f'<span class="muted small">{a["gp"]} GP · {a["min"] / a["gp"]:.0f} min · {a["pts"] / a["gp"]:.1f} pts · '
        f'{a["reb"] / a["gp"]:.1f} reb · {a["ast"] / a["gp"]:.1f} ast</span></div>'
        f'<span class="imp-pm">{a["pm"] / a["gp"]:+.1f}</span></li>' for i, a in enumerate(top, 1))
    return f"""
    <div class="card"><h3>Top {IMPACT_TOP} Lakers impact</h3>
      <ul class="impact">{items or '<li class="muted">Not enough games yet.</li>'}</ul>
      <p class="muted small">Ranked by plus/minus per game: how many points the Lakers outscore opponents by while
      the player is on the court. Rotation players only (15+ minutes, 40%+ of games).</p></div>"""


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

    stat_rows = "".join(
        f'<tr><th scope="row">{lbl}</th><td>{fmt(lal[k])}</td>'
        f'<td>{ordinal(ranks[k][LAKERS_ID]) if k in ranks else "–"}</td>'
        f'<td>{rank_bar(ranks[k][LAKERS_ID], 30) if k in ranks else ""}</td></tr>'
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
    <div class="card"><h3>Stats & league rank</h3>
      <div class="chips" role="group" aria-label="Choose team or player">
        <button type="button" class="chip on" data-stat="rank-lal" aria-pressed="true">Lakers</button>
        <button type="button" class="chip" data-stat="rank-luka" aria-pressed="false">Luka</button>
      </div>
      <div class="stat-panel" data-stat="rank-lal">
      <table class="kv"><thead><tr><th></th><th>Per game</th><th>Rank</th><th></th></tr></thead>
      <tbody>{stat_rows}</tbody></table>
      <p class="muted small">Ratings are per 100 possessions, estimated from box-score totals.
      Rank is out of 30 teams (1st = best).</p></div>
      <div class="stat-panel" data-stat="rank-luka" hidden>{luka_rank_table(d)}</div></div>
    <div class="card"><h3>Last 5 games</h3><ul class="results">{''.join(res)}</ul>
      <h3 style="margin-top:20px">Western Conference</h3>
      <div class="tablewrap"><table class="stand"><thead><tr><th>#</th><th>Team</th><th>W-L</th><th>GB</th>
      <th>Strk</th><th>L10</th><th>Net</th></tr></thead><tbody>{stand}</tbody></table></div>
      <p class="muted small">Lines mark the playoff (1–6) and play-in (7–10) spots.</p></div>
  </div>
  <div class="grid2">
    {schedule_card(d)}
    {impact_card(d)}
  </div>
  <div class="card"><h3>Lakers vs. the league: offense and defense</h3>
    <div class="chips conf-chips" role="group" aria-label="Choose conference">
      <button type="button" class="chip on" data-conf="all" aria-pressed="true">Both</button>
      <button type="button" class="chip" data-conf="east" aria-pressed="false">East</button>
      <button type="button" class="chip" data-conf="west" aria-pressed="false">West</button>
    </div>
    <div class="legend"><span><i class="sw hi"></i>Lakers</span><span><i class="sw opp"></i>Next opponent</span>
    <span><i class="sw east"></i>East</span><span><i class="sw west"></i>West</span></div>
    {chart_ratings(teams, opp_id)}
    <p class="muted small">The Lakers and their next opponent stay visible in every view. The dashed lines are the
    league averages.</p></div>
</section>"""


def next_game_card(d):
    teams = {t["team_id"]: t for t in d["teams"]}
    upcoming = [g for g in d["games"] if g["status"] != "STATUS_FINAL"]
    if not upcoming:
        return '<div class="card"><h3>Next game</h3><p class="muted">No upcoming games scheduled.</p></div>'
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
  <div class="card matchup">
    <h3 class="card-kicker">Next game</h3>
    <div class="mu-head">
      <div class="mu-team"><img src="{lal['logo']}" alt="" class="biglogo"><strong>Lakers</strong>
        <span class="muted">{lal['wins']}-{lal['losses']} · {ordinal(lal['seed'])} West</span></div>
      <div class="mu-mid"><div class="mu-at">{'vs' if home else '@'}</div>
        <div class="mu-when">{when:%A %d %B}</div><div class="mu-time">{when:%H:%M} Ljubljana</div></div>
      <div class="mu-team"><img src="{opp['logo']}" alt="" class="biglogo"><strong>{escape(opp['name'].split()[-1])}</strong>
        <span class="muted">{opp['wins']}-{opp['losses']} · {ordinal(opp['seed'])} {opp['conference']} · {opp['streak']}</span></div>
    </div>
    <table class="vs"><thead><tr><th>LAL</th><th>Season averages</th><th>{opp['abbr']}</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table>
    <p class="muted small">Small number = league rank out of 30. The team with the edge on each stat is highlighted.</p>
    <div class="grid2 inj2">
      <div><h3 class="sub">Injuries · Lakers</h3><ul class="inj">{inj(LAKERS_ID)}</ul></div>
      <div><h3 class="sub">Injuries · {escape(opp['name'])}</h3><ul class="inj">{inj(opp_id)}</ul></div>
    </div>
    <h3 class="sub">Head-to-head this season</h3><ul class="results">{h2h_html}</ul>
  </div>"""


# ---------- finished games (box scores) ----------

def nickname(abbr, teams):
    names = {t["abbr"]: t["name"] for t in teams}
    return names.get(abbr, abbr).split()[-1]


def game_youtube(info, teams):
    us = datetime.strptime(info["us_date"], "%Y%m%d")
    q = (f"{nickname(info['away_abbr'], teams)} vs {nickname(info['home_abbr'], teams)} "
         f"highlights {us:%B} {us.day}, {us.year}")
    return "https://www.youtube.com/results?search_query=" + quote_plus(q)


def highlight_buttons(info, teams, youtube=None):
    out = []
    if info.get("highlights_url"):
        out.append(f'<a class="btn-yt" href="{escape(info["highlights_url"])}" target="_blank" rel="noopener">'
                   f'<span aria-hidden="true">▶</span> Highlights (ESPN)</a>')
    out.append(f'<a class="btn-yt{" ghost" if out else ""}" href="{youtube or game_youtube(info, teams)}" '
               f'target="_blank" rel="noopener"><span aria-hidden="true">▶</span> '
               f'{"YouTube" if out else "Highlights (YouTube)"}</a>')
    return f'<div class="btns">{"".join(out)}</div>'


def quarter_table(info):
    h, a = info["home_lines"].split(","), info["away_lines"].split(",")
    n = max(len(h), len(a))
    head = "".join(f"<th>{q + 1 if q < 4 else 'OT' if q == 4 else f'{q - 3}OT'}</th>" for q in range(n))
    row = lambda abbr, ls, total: (f'<tr><th scope="row">{abbr}</th>' + "".join(f"<td>{x}</td>" for x in ls)
                                   + f'<td class="tot">{total}</td></tr>')
    return (f'<table class="qtr"><thead><tr><th></th>{head}<th>T</th></tr></thead><tbody>'
            f'{row(info["away_abbr"], a, info["away_score"])}{row(info["home_abbr"], h, info["home_score"])}'
            f'</tbody></table>')


def box_rows(a, b, a_pts, b_pts):
    """Team-vs-team rows for one game: (label, a text, b text, which side has the edge)."""
    pct = lambda m, t: 100 * m / t if t else None
    g = lambda box, k: box.get(k) or 0
    defs = [
        ("Points", a_pts, b_pts, False, "{:.0f}"),
        ("Field goals", f"{g(a, 'fgm'):.0f}-{g(a, 'fga'):.0f}", f"{g(b, 'fgm'):.0f}-{g(b, 'fga'):.0f}", None, None),
        ("Field goal %", pct(g(a, "fgm"), g(a, "fga")), pct(g(b, "fgm"), g(b, "fga")), False, "{:.1f}%"),
        ("3-pointers", f"{g(a, 'fg3m'):.0f}-{g(a, 'fg3a'):.0f}", f"{g(b, 'fg3m'):.0f}-{g(b, 'fg3a'):.0f}", None, None),
        ("3-point %", pct(g(a, "fg3m"), g(a, "fg3a")), pct(g(b, "fg3m"), g(b, "fg3a")), False, "{:.1f}%"),
        ("Free throws", f"{g(a, 'ftm'):.0f}-{g(a, 'fta'):.0f}", f"{g(b, 'ftm'):.0f}-{g(b, 'fta'):.0f}", None, None),
        ("Free throw %", pct(g(a, "ftm"), g(a, "fta")), pct(g(b, "ftm"), g(b, "fta")), False, "{:.1f}%"),
        ("Offensive rebounds", g(a, "oreb"), g(b, "oreb"), False, "{:.0f}"),
        ("Defensive rebounds", g(a, "dreb"), g(b, "dreb"), False, "{:.0f}"),
        ("Total rebounds", g(a, "reb"), g(b, "reb"), False, "{:.0f}"),
        ("Assists", g(a, "ast"), g(b, "ast"), False, "{:.0f}"),
        ("Steals", g(a, "stl"), g(b, "stl"), False, "{:.0f}"),
        ("Blocks", g(a, "blk"), g(b, "blk"), False, "{:.0f}"),
        ("Turnovers", g(a, "tov"), g(b, "tov"), True, "{:.0f}"),
        ("Fouls", g(a, "pf"), g(b, "pf"), True, "{:.0f}"),
        ("Points in the paint", g(a, "paint"), g(b, "paint"), False, "{:.0f}"),
        ("Fast-break points", g(a, "fastbreak"), g(b, "fastbreak"), False, "{:.0f}"),
        # pts_off_tov is what a team conceded after its own turnovers, i.e. what the other side scored
        ("Points off turnovers", g(b, "pts_off_tov"), g(a, "pts_off_tov"), False, "{:.0f}"),
        ("Largest lead", g(a, "largest_lead"), g(b, "largest_lead"), False, "{:.0f}"),
        ("Time in the lead", g(a, "lead_pct"), g(b, "lead_pct"), False, "{:.0f}%"),
    ]
    rows = []
    for lbl, x, y, low, f in defs:
        edge = None
        if low is not None and x is not None and y is not None and x != y:
            edge = "a" if (x < y) == low else "b"
        show = lambda v: "–" if v is None else (f.format(v) if f else v)
        rows.append(f'<tr><td class="{"edge" if edge == "a" else ""}">{show(x)}</td><th scope="row">{lbl}</th>'
                    f'<td class="{"edge" if edge == "b" else ""}">{show(y)}</td></tr>')
    return "".join(rows)


def player_table(players, team_id, limit=None):
    rows = sorted((p for p in players if p["team_id"] == team_id and p.get("min")),
                  key=lambda p: (-p["starter"], -(p["min"] or 0)))[:limit]
    body = "".join(
        f'<tr><th scope="row">{escape(short_name(p["name"]))}{" <small>S</small>" if p["starter"] else ""}</th>'
        f'<td>{p["min"]:.0f}</td><td class="strong">{p["pts"]:.0f}</td><td>{p["reb"]:.0f}</td>'
        f'<td>{p["ast"]:.0f}</td><td>{p["fgm"]:.0f}-{p["fga"]:.0f}</td><td>{p["fg3m"]:.0f}-{p["fg3a"]:.0f}</td>'
        f'<td>{p["ftm"]:.0f}-{p["fta"]:.0f}</td><td>{p["oreb"]:.0f}</td><td>{p["dreb"]:.0f}</td>'
        f'<td>{p["stl"]:.0f}</td><td>{p["blk"]:.0f}</td><td>{p["tov"]:.0f}</td><td>{p["pf"]:.0f}</td>'
        f'<td>{(p["plus_minus"] or 0):+.0f}</td></tr>' for p in rows)
    return (f'<div class="tablewrap"><table class="box"><thead><tr><th>Player</th><th>MIN</th><th>PTS</th>'
            f'<th>REB</th><th>AST</th><th>FG</th><th>3PT</th><th>FT</th><th>OREB</th><th>DREB</th><th>STL</th>'
            f'<th>BLK</th><th>TO</th><th>PF</th><th>+/-</th></tr></thead><tbody>{body}</tbody></table></div>')


def game_story(info, box, players, teams):
    """Winner/loser, reasons, key player and tags for one game."""
    home_won = info["home_score"] > info["away_score"]
    w_id, l_id = (info["home_id"], info["away_id"]) if home_won else (info["away_id"], info["home_id"])
    w_abbr, l_abbr = (info["home_abbr"], info["away_abbr"]) if home_won else (info["away_abbr"], info["home_abbr"])
    w_box, l_box = box.get(w_id, {}), box.get(l_id, {})
    kp = insights.key_player(players, w_id)
    return {
        "w_id": w_id, "l_id": l_id, "w_abbr": w_abbr,
        "reasons": insights.reasons(info, w_box, l_box, nickname(w_abbr, teams), nickname(l_abbr, teams)),
        "key": kp, "key_line": insights.stat_line(kp) if kp else "",
        "tags": insights.tags(info, l_box),
    }


def story_html(story, teams):
    reasons = "".join(f"<li>{escape(r)}</li>" for r in story["reasons"]) or "<li>No single stat stood out</li>"
    key = (f'<p class="keyp"><span class="lbl-k">Key player</span> <strong>{escape(story["key"]["name"])}</strong>'
           f' · {story["key_line"]}</p>') if story["key"] else ""
    why = (f'<p class="lbl-k">Why the {escape(nickname(story["w_abbr"], teams))} won</p>'
           f'<ul class="why">{reasons}</ul>') if reasons else ""
    return why + key


def last_game_card(d):
    g = d["lakers_last"]
    if not g:
        return '<div class="card"><h3>Last game</h3><p class="muted">No finished games yet.</p></div>'
    teams = {t["team_id"]: t for t in d["teams"]}
    box, players = d["box"].get(g["event_id"], {}), d["box_players"].get(g["event_id"], [])
    home = g["home_id"] == LAKERS_ID
    opp_id = g["away_id"] if home else g["home_id"]
    lal_pts, opp_pts = (g["home_score"], g["away_score"]) if home else (g["away_score"], g["home_score"])
    won = lal_pts > opp_pts
    opp = teams[opp_id]
    story = game_story(g, box, players, d["teams"])
    luka = next((x for x in d["luka_games"] if x["event_id"] == g["event_id"]), None)
    youtube = highlights_url(luka, d["teams"]) if luka else None
    return f"""
  <div class="card">
    <h3 class="card-kicker">Last game · {local(g['start_utc']):%A %d %B}</h3>
    <div class="mu-head">
      <div class="mu-team"><img src="{teams[LAKERS_ID]['logo']}" alt="" class="biglogo"><strong>Lakers</strong></div>
      <div class="mu-mid"><div class="mu-at">{'vs' if home else '@'} · <span class="wl {'W' if won else 'L'}">{'W' if won else 'L'}</span></div>
        <div class="score">{lal_pts}<span class="muted">–</span>{opp_pts}</div>
        {''.join(f'<span class="tag">{t}</span>' for t in story['tags'])}</div>
      <div class="mu-team"><img src="{opp['logo']}" alt="" class="biglogo"><strong>{escape(opp['name'].split()[-1])}</strong></div>
    </div>
    {quarter_table(g)}
    {f'<p class="headline">{escape(g["headline"])}</p>' if g.get("headline") else ''}
    {story_html(story, d['teams'])}
    {highlight_buttons(g, d['teams'], youtube)}
    <table class="vs"><thead><tr><th>LAL</th><th>Game stats</th><th>{opp['abbr']}</th></tr></thead>
    <tbody>{box_rows(box.get(LAKERS_ID, {}), box.get(opp_id, {}), lal_pts, opp_pts)}</tbody></table>
    <h3 class="sub">Lakers box score</h3>
    {player_table(players, LAKERS_ID)}
  </div>"""


def section_games(d):
    return f"""
<section id="next">
  <h2>Lakers games</h2>
  <div class="chips ln-chips" role="group" aria-label="Choose game">
    <button type="button" class="chip on" data-panel="last" aria-pressed="true">Last game</button>
    <button type="button" class="chip" data-panel="next" aria-pressed="false">Next game</button>
  </div>
  <div class="ln-panel" data-panel="last">{last_game_card(d)}</div>
  <div class="ln-panel off" data-panel="next">{next_game_card(d)}</div>
</section>"""


def game_card(info, d):
    teams = {t["team_id"]: t for t in d["teams"]}
    box, players = d["box"].get(info["event_id"], {}), d["box_players"].get(info["event_id"], [])
    story = game_story(info, box, players, d["teams"])

    def team_row(side):
        tid, abbr, pts = info[f"{side}_id"], info[f"{side}_abbr"], info[f"{side}_score"]
        t = teams.get(tid, {})
        win = tid == story["w_id"]
        return (f'<div class="gc-team{" win" if win else ""}"><img src="{t.get("logo", "")}" alt="" class="logo">'
                f'<span class="gc-name">{escape(t.get("name", abbr))}</span>'
                f'<span class="muted small">{t.get("wins", "")}-{t.get("losses", "")}</span>'
                f'<span class="gc-pts">{pts}</span></div>')

    lakers = LAKERS_ID in (info["home_id"], info["away_id"])
    tops = "".join(
        f'<h4>{escape(teams.get(tid, {}).get("name", ""))}</h4>{player_table(players, tid, 5)}'
        for tid in (info["away_id"], info["home_id"]))
    return f"""
<article class="card gc{' me' if lakers else ''}">
  <div class="gc-tags">{''.join(f'<span class="tag">{t}</span>' for t in story['tags'])}
    <span class="muted small">Final{f" · ended {local(info['end_utc']):%H:%M} Ljubljana" if info.get('end_utc') else ''}</span></div>
  {team_row('away')}{team_row('home')}
  {story_html(story, d['teams'])}
  <p class="headline">{escape(info.get("headline") or "")}</p>
  {highlight_buttons(info, d['teams'])}
  <details><summary>Box score</summary>
    {quarter_table(info)}
    <table class="vs"><thead><tr><th>{info['away_abbr']}</th><th></th><th>{info['home_abbr']}</th></tr></thead>
    <tbody>{box_rows(box.get(info['away_id'], {}), box.get(info['home_id'], {}),
                     info['away_score'], info['home_score'])}</tbody></table>
    {tops}
  </details>
</article>"""


# ---------- page ----------

CSS = """
:root{color-scheme:light;--page:#f6f5f2;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--hi:#6250d6;--hi-wash:rgba(98,80,214,.08);
--opp:#eb6834;--base:#b9b8b0;--edge:rgba(98,80,214,.12);--good:#006300;--bad:#d03b3b;
--s1:#6250d6;--s2:#eb6834;--s3:#1baf7a;--s4:#2a78d6;--s5:#e87ba4;--east:#9db4d3;--west:#dba4a9}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,.10);--hi:#9085e9;--hi-wash:rgba(144,133,233,.12);--opp:#d95926;--base:#5a5955;
--edge:rgba(144,133,233,.18);--good:#0ca30c;--s1:#9085e9;--s2:#d95926;--s3:#199e70;--s4:#3987e5;--s5:#d55181;--east:#4f6684;--west:#7f5258}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--hi:#9085e9;--hi-wash:rgba(144,133,233,.12);
--opp:#d95926;--base:#5a5955;--edge:rgba(144,133,233,.18);--good:#0ca30c;--s1:#9085e9;--s2:#d95926;
--s3:#199e70;--s4:#3987e5;--s5:#d55181;--east:#4f6684;--west:#7f5258}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header.top{position:sticky;top:0;z-index:5;background:var(--page);border-bottom:1px solid var(--border)}
.bar-in{max-width:1080px;margin:0 auto;padding:10px 16px;display:flex;gap:16px;align-items:center;overflow-x:auto}
.brand{font-weight:700;white-space:nowrap;color:var(--ink);text-decoration:none}
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
.lastgame{background:var(--hi-wash);border-radius:10px;padding:10px 14px;margin:0 0 14px;display:flex;
align-items:center;gap:12px;flex-wrap:wrap}.lastgame p{margin:0;flex:1 1 300px}
.btn-yt{flex:none;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:999px;
background:var(--hi);color:var(--surface);text-decoration:none;font-weight:600;font-size:14px}
.btn-yt:hover{filter:brightness(1.1)}.btn-yt:focus-visible{outline:2px solid var(--hi);outline-offset:2px}
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
.chart .dot.east{fill:var(--east)}.chart .dot.west{fill:var(--west)}
.sw.east{background:var(--east)}.sw.west{background:var(--west)}
.scatter[data-conf="east"] .conf-west:not(.pinned),.scatter[data-conf="west"] .conf-east:not(.pinned){display:none}
.ln-panel.off{display:none}.ln-panel>.card{margin-top:0}
.inj2{gap:16px}.inj2 h3.sub{margin-top:18px}
.card-kicker{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink2);margin:0 0 8px}
h3.sub{margin:18px 0 6px;font-size:14px}
.score{font-size:30px;font-weight:700;letter-spacing:-.01em}.score .muted{margin:0 4px}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;margin:2px;
background:var(--hi-wash);color:var(--ink2)}
table.qtr{margin:4px auto 10px;width:auto;font-size:13px}table.qtr th,table.qtr td{padding:3px 9px;text-align:center}
table.qtr td.tot{font-weight:700}table.qtr th[scope=row]{text-align:left}
.headline{font-style:italic;color:var(--ink2);margin:8px 0;font-size:14px}
.lbl-k{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink2);margin:10px 0 4px}
ul.why{margin:0 0 6px;padding-left:18px}ul.why li{margin:2px 0}
.keyp{margin:8px 0}.keyp .lbl-k{display:inline;margin-right:6px}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.btn-yt.ghost{background:transparent;color:var(--ink);border:1px solid var(--border)}
table.box{font-size:13px}table.box th[scope=row]{text-align:left;position:sticky;left:0;background:var(--surface)}
table.box th[scope=row] small{color:var(--muted);font-size:10px}table.box td.strong{font-weight:700}
table.vs thead th{font-size:12px}
.games{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px;align-items:stretch}
@media (max-width:760px){.games{grid-template-columns:1fr}}
.gc{margin-top:0;display:flex;flex-direction:column}
.gc ul.why{min-height:3.2em}.gc .keyp{min-height:3em}
.gc .headline{min-height:4.4em;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.gc .btns{margin-top:auto}.gc.me{box-shadow:inset 0 0 0 2px var(--hi)}
.gc-tags{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin-bottom:6px}
.gc-team{display:flex;align-items:center;gap:8px;padding:5px 0;color:var(--ink2)}
.gc-team.win{color:var(--ink);font-weight:700}.gc-name{flex:1}.gc-pts{font-size:22px;font-variant-numeric:tabular-nums}
.gc details{margin-top:8px;border-top:1px solid var(--grid);padding-top:8px}
.gc summary{cursor:pointer;font-weight:600;color:var(--ink2)}.gc h4{margin:14px 0 4px;font-size:13px}
nav a.active{color:var(--ink);font-weight:600}
.hero .tiles{grid-template-columns:repeat(7,1fr)}
@media (max-width:1000px){.hero .tiles{grid-template-columns:repeat(4,1fr)}}
@media (max-width:560px){.hero .tiles{grid-template-columns:repeat(2,1fr)}}.tile-s .warn{color:var(--bad);font-weight:600}
.ms-grid{display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:24px}
@media (max-width:900px){.ms-grid{grid-template-columns:1fr}}
.ms-items{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.ms-item{background:var(--page);border-radius:10px;padding:8px 10px}
.ms-v{font-size:22px;font-weight:700}.ms-k{font-size:12px;color:var(--ink2)}.ms-s{font-size:11px;color:var(--muted)}
.mile{margin-bottom:10px}.mile-top{display:flex;justify-content:space-between;font-size:14px}
.rbar.wide{width:100%;margin:4px 0 2px}
ul.bests{list-style:none;margin:0;padding:0}ul.bests li{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;
padding:6px 0;border-bottom:1px solid var(--grid)}ul.bests li>span:first-child{width:86px;color:var(--ink2);font-size:13px}
ul.bests li.lead{font-weight:600}ul.bests li.lead strong{color:var(--hi)}
.records{margin-top:14px;padding-top:10px;border-top:1px solid var(--grid)}
.records ul{margin:4px 0 0;padding-left:18px;font-size:14px}.records a{color:var(--ink)}
.verdict{margin:0 0 8px;font-size:14px}
table.sched th[scope=row]{text-align:left;display:flex;align-items:center;gap:6px}
.lvl{font-weight:600}.lvl.hard{color:var(--bad)}.lvl.easy{color:var(--good)}
.tag.b2b{background:rgba(208,59,59,.12);color:var(--bad)}
ul.impact{list-style:none;margin:0;padding:0}ul.impact li{display:flex;align-items:center;gap:12px;padding:10px 0;
border-bottom:1px solid var(--grid)}.imp-rank{width:26px;height:26px;border-radius:50%;background:var(--hi-wash);
display:grid;place-items:center;font-weight:700;color:var(--hi);flex:none}
.imp-main{flex:1;display:flex;flex-direction:column}.imp-pm{font-size:22px;font-weight:700;color:var(--good)}
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
card.querySelectorAll('.stat-panel').forEach(p=>p.hidden=p.dataset.stat!==b.dataset.stat);
if(b.dataset.panel)card.querySelectorAll('.ln-panel').forEach(p=>p.classList.toggle('off',p.dataset.panel!==b.dataset.panel));
if(b.dataset.conf)card.querySelectorAll('.scatter').forEach(s=>s.dataset.conf=b.dataset.conf);
tip.style.opacity=0})});
"""


def section_last_night(d):
    night = d["last_night"]
    if not night:
        return '<section class="hero"><h1>Last night</h1><p class="muted">No finished games yet.</p></section>'
    games = [g for g in d["game_info"] if g["us_date"] == night]
    games.sort(key=lambda g: (LAKERS_ID not in (g["home_id"], g["away_id"]), g["start_utc"]))
    us = datetime.strptime(night, "%Y%m%d")
    return f"""
<section class="hero">
  <h1>Last night in the NBA</h1>
  <p class="muted">{us:%A} {us.day} {us:%B} (US date) · {len(games)} games · "Why they won" is worked out from
  the box score, and the key player is the winner's top performer by Game Score.</p>
  <div class="games">{''.join(game_card(g, d) for g in games)}</div>
</section>"""


def page(title, active, body, d):
    now = datetime.now(TZ)
    links = [("index.html#next", "Lakers games", "home"), ("index.html#mvp", "MVP race", "home"),
             ("index.html#compare", "Compare", "home"), ("index.html#lakers", "Lakers", "home"),
             ("last-night.html", "Last night", "night")]
    nav = "".join(f'<a href="{h}"{" class=\"active\"" if k == active == "night" else ""}>{t}</a>'
                  for h, t, k in links)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><meta name="description" content="Daily Luka Dončić, MVP race and Lakers tracker">
<style>{CSS}</style></head><body>
<header class="top"><div class="bar-in"><a class="brand" href="index.html">Luka Watch</a>
<nav>{nav}</nav>
<span class="updated">Updated {now:%a %d %b, %H:%M}</span></div></header>
<main>{body}</main>
<footer>Data: ESPN (stats, standings, schedule, box scores, injuries) and Polymarket (MVP odds). Times in Ljubljana
time. Ratings are estimates from box-score totals. Snapshot {d['date']}.</footer>
<div id="tip" role="tooltip"></div><script>{JS}</script></body></html>"""


def build():
    d = load()
    home = (section_hero(d) + section_milestones(d) + section_games(d) + section_mvp(d) + section_compare(d)
            + section_lakers(d))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page("Luka Watch", "home", home, d), encoding="utf-8")
    (OUT.parent / "last-night.html").write_text(page("Last night · Luka Watch", "night", section_last_night(d), d),
                                                encoding="utf-8")
    print(f"Wrote {OUT} and last-night.html")


if __name__ == "__main__":
    build()
