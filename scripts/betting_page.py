"""Inner HTML of the betting page (encrypted before publishing; see run_betting.py)."""
import json
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from model import betting

TZ = ZoneInfo("Europe/Ljubljana")
CATEGORY = {"minutes": "Minutes", "efficiency": "Off/big night", "close": "Close call", "model": "Model",
            "push": "Push", "hit": "Hit"}


def _local(ts, fmt="%a %d %b, %H:%M"):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ).strftime(fmt) if ts else ""


def _odds(o):
    try:
        v = float(o)
        return f"{v:+.0f}"
    except (TypeError, ValueError):
        return str(o or "")


def eur(v, sign=False):
    return f"{'+' if sign and v > 0 else ''}{'-' if v < 0 else ''}€{abs(v):,.2f}"


def pick_card(p, bank):
    reasons = "".join(f"<li>{escape(r)}</li>" for r in json.loads(p["reasons"] or "[]"))
    stake = round(bank * p["units"] / 100, 2)
    payout = stake * p["dec"]
    expected = stake * p["ev"]
    worse = "higher" if p["side"] == "over" else ("lower" if p["side"] == "under" else "worse")
    play = (f'Still worth it up to <strong>{p["playable_to"]:g}</strong>; skip if your bookmaker\'s line is {worse}.'
            if p["playable_to"] is not None and p["playable_to"] != p["line"] else
            "Only worth it at this exact line or better.")
    return f"""
<article class="card bet">
  <div class="bet-top"><span class="tag">{escape(p['game'])}</span>
    <span class="muted small">Tip-off {_local(p['tip_utc'])} · updated {_local(p['last_seen'], '%H:%M')}</span></div>
  <h3 class="bet-title">{escape(p['player'])}</h3>
  <div class="bet-nums">
    <div><span class="lbl-k">Odds</span><strong>{_odds(p['odds'])}</strong><span class="muted small">{p['dec']:.2f}</span></div>
    <div><span class="lbl-k">Stake</span><strong>{eur(stake)}</strong>
      <span class="muted small">{p['units']:g}% of {eur(bank)}</span></div>
    <div><span class="lbl-k">Our chance</span><strong>{p['p_final'] * 100:.0f}%</strong>
      <span class="muted small">market {p['fair'] * 100:.0f}%</span></div>
    <div><span class="lbl-k">Edge</span><strong class="pos">{p['edge'] * 100:+.1f}</strong>
      <span class="muted small">EV {p['ev'] * 100:+.1f}%</span></div>
  </div>
  <p class="money">If it wins: pays <strong>{eur(payout)}</strong> (profit {eur(payout - stake)}) ·
    if it loses: {eur(-stake)} · expected value <strong class="{'pos' if expected > 0 else 'neg'}">{eur(expected, True)}</strong></p>
  <p class="play">{play}</p>
  {where_to_bet(p)}
  <p class="lbl-k">Why this bet</p><ul class="why">{reasons}</ul>
</article>"""


def where_to_bet(p):
    """Where this exact bet can be placed, and at what price."""
    rows = [f'<li><strong>DraftKings</strong> (US): {p["line"]:g} at {_odds(p["odds"])} ({p["dec"]:.2f}), '
            f'the line the model used</li>']
    poly = json.loads(p["poly"]) if p.get("poly") else None
    if poly and poly.get("dec"):
        same = poly["line"] == p["line"]
        ev = p["p_final"] * poly["dec"] - 1 if same else None
        better = " · <span class='pos'>better price than DraftKings</span>" if same and poly["dec"] > p["dec"] else ""
        rows.append(f'<li><strong>Polymarket</strong>: {"same line" if same else f"line {poly["line"]:g} (not the same)"}, '
                    f'price {poly["price"]:.2f} = {poly["dec"]:.2f} odds'
                    + (f', expected value {ev * 100:+.1f}%' if ev is not None else "")
                    + f'{better} <span class="muted small">(${poly["liquidity"]:,} in the market)</span></li>')
    else:
        rows.append('<li><strong>Polymarket</strong>: no matching market with real money behind it</li>')
    rows.append(f'<li><strong>bet365 / your bookmaker</strong>: compare their line with "still worth it up to" above</li>')
    return f'<p class="lbl-k">Where to bet</p><ul class="where">{"".join(rows)}</ul>'


def result_row(p, money):
    reasons = "".join(f"<li>{escape(r)}</li>" for r in json.loads(p["reasons"] or "[]"))
    res = {"W": "W", "L": "L", "P": "P"}.get(p["result"], p["result"])
    m = money.get(p["pick_id"], {"stake": 0, "profit": 0, "after": 0})
    profit = eur(m["profit"], True)
    clv = (f' · closing line {p["close_line"]:g} ({_odds(p["close_odds"])})' if p["close_line"] is not None else "")
    return f"""
<details class="res">
  <summary><span class="wl {res}">{res}</span> <strong>{escape(p['player'])}</strong>
    <span class="muted small">{escape(p['game'])} · {_local(p['tip_utc'], '%d %b')} · {_odds(p['odds'])} · stake {eur(m['stake'])}</span>
    <span class="res-profit {'pos' if m['profit'] > 0 else 'neg' if m['profit'] < 0 else ''}">{profit}</span></summary>
  <p><span class="tag">{CATEGORY.get(p['miss_category'], p['miss_category'] or '')}</span> {escape(p['miss_text'] or '')}</p>
  <p class="muted small">Stake {eur(m['stake'])} at {p['dec']:.2f} → {eur(m['profit'], True)} · bankroll after: {eur(m['after'])}</p>
  <p class="muted small">Our chance {p['p_final'] * 100:.0f}% vs market {p['fair'] * 100:.0f}%{clv}</p>
  <p class="lbl-k">Reasoning at the time</p><ul class="why">{reasons}</ul>
</details>"""


def bankroll_chart(curve):
    """Simulated bankroll after each betting day, with the €100 start as a reference line."""
    if len(curve) < 2:
        return ('<p class="muted small">The chart starts after the first graded betting day; '
                f'the bankroll stands at {eur(curve[-1][1])}.</p>')
    W, H, L, R, T, B = 640, 220, 52, 14, 14, 26
    vals = [v for _, v in curve]
    lo, hi = min(vals + [betting.STARTING_BANKROLL]), max(vals + [betting.STARTING_BANKROLL])
    pad = max((hi - lo) * 0.15, 2)
    lo, hi = lo - pad, hi + pad
    x = lambda i: L + (W - L - R) * i / (len(curve) - 1)
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    ticks = "".join(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                    f'<text x="{L - 6}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">€{v:.0f}</text>'
                    for v in (lo + pad, (lo + hi) / 2, hi - pad))
    start = (f'<line x1="{L}" x2="{W - R}" y1="{y(betting.STARTING_BANKROLL):.1f}" '
             f'y2="{y(betting.STARTING_BANKROLL):.1f}" class="ref"/>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(curve))
    dots = "".join(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.5" class="dot-hi"/>'
                   f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="14" class="hit" data-tip="'
                   f'{escape(("Start" if d == "start" else datetime.strptime(d, "%Y%m%d").strftime("%a %d %b")) + "|" + eur(v))}" tabindex="0"/>'
                   for i, (d, v) in enumerate(curve))
    last = curve[-1][1]
    label = (f'<text x="{x(len(curve) - 1) - 6:.1f}" y="{y(last) - 10:.1f}" class="val" text-anchor="end">'
             f'{eur(last)}</text>')
    first, final = curve[1][0], curve[-1][0]
    axis = (f'<text x="{L}" y="{H - 6}" class="tick">{datetime.strptime(first, "%Y%m%d"):%d %b}</text>'
            f'<text x="{W - R}" y="{H - 6}" class="tick" text-anchor="end">{datetime.strptime(final, "%Y%m%d"):%d %b}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Simulated bankroll">'
            f'{ticks}{start}<path d="{path}" class="line-hi"/>{dots}{label}{axis}</svg>')


def render(con):
    active = [dict(r) for r in con.execute(
        "SELECT * FROM picks WHERE status='active' ORDER BY tip_utc, ev DESC")]
    withdrawn = [dict(r) for r in con.execute(
        "SELECT * FROM picks WHERE status='withdrawn' ORDER BY last_seen DESC LIMIT 20")]
    rec = betting.record(con)
    correct = [r for r in rec["rows"] if r["result"] == "W"]
    wrong = [r for r in rec["rows"] if r["result"] == "L"]
    pushes = [r for r in rec["rows"] if r["result"] == "P"]
    stats = betting.get_setting(con, "learning_stats", {})
    log = [dict(r) for r in con.execute("SELECT * FROM learning_log ORDER BY logged_at DESC LIMIT 10")]
    decided = rec["w"] + rec["l"]
    hit = f"{100 * rec['w'] / decided:.0f}%" if decided else "–"
    bank = betting.bankroll(con)
    money = bank["picks"]
    profit_eur = bank["value"] - betting.STARTING_BANKROLL

    picks_html = "".join(pick_card(p, bank["value"]) for p in active) or (
        '<div class="card"><p>No picks right now. Either tonight\'s Lakers props aren\'t posted yet '
        '(DraftKings usually posts them on game day; lines are checked at 16:23, 20:23, 00:13 and 03:13), '
        'or nothing clears the bar: our probability must beat the market by 5+ points with +3% expected value.</p></div>')
    misses = " · ".join(f"{CATEGORY.get(k, k)} {v}" for k, v in sorted(rec["misses"].items(), key=lambda x: -x[1])) or "–"
    roi = f"{100 * profit_eur / bank['staked']:+.1f}% on {eur(bank['staked'])} staked" if bank["staked"] else "nothing staked yet"
    tiles = [("Bankroll", eur(bank["value"]), f"started at {eur(betting.STARTING_BANKROLL)} · "
              f"{100 * profit_eur / betting.STARTING_BANKROLL:+.1f}%"),
             ("Profit", eur(profit_eur, True), roi),
             ("Record", f"{rec['w']}-{rec['l']}" + (f"-{rec['p']}" if rec["p"] else ""), f"hit rate {hit}"),
             ("Closing line value", f"{rec['clv']:+.1f}" if rec["clv"] is not None else "–",
              "points of probability; > 0 = beating the market"),
             ("Why misses happened", str(len(wrong)), misses)]
    tiles_html = "".join(f'<div class="tile"><div class="tile-k">{k}</div><div class="tile-v">{v}</div>'
                         f'<div class="tile-s">{escape(s)}</div></div>' for k, v, s in tiles)
    learn_rows = "".join(
        f"<tr><th scope='row'>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in [
            ("Graded projections (all teams)", stats.get("graded_predictions", 0)),
            ("Average projection error (actual − projected)", stats.get("bias", "–")),
            ("Average minutes error", stats.get("minutes_bias", "–")),
            ("Log loss: our model / market / combined", f"{stats.get('logloss_model', '–')} / "
             f"{stats.get('logloss_market', '–')} / {stats.get('logloss_current', '–')}"),
            ("Status", stats.get("status", "–"))])
    log_html = "".join(f"<li><span class='muted small'>{_local(e['logged_at'])}</span> {escape(e['text'])}</li>"
                       for e in log) or "<li class='muted'>No recalibrations yet.</li>"
    return f"""
<section class="hero">
  <h1>Lakers bets <span class="tag">pilot · paper trading</span></h1>
  <p class="muted">Model picks on Lakers games only, from real DraftKings lines. Paper trading: tracked as if
  bet with a simulated {eur(betting.STARTING_BANKROLL)} bankroll, no real money, until there are enough graded picks
  to judge the model.</p>
</section>
<section>
  <h2>Tonight's picks</h2>
  <div class="bets">{picks_html}</div>
</section>
<section>
  <h2>Track record</h2>
  <div class="tiles">{tiles_html}</div>
  <div class="card"><h3>Simulated bankroll</h3>{bankroll_chart(bank['curve'])}
    <p class="muted small">Starts at {eur(betting.STARTING_BANKROLL)}. Each night's stakes are a share of the bankroll at
    the start of that day (0.5–2%, more when the edge is bigger), so stakes grow after winning nights and shrink after
    losing ones. Simulated only: no real money.</p></div>
  <details class="list"><summary>Correct ({len(correct)})</summary>{''.join(result_row(p, money) for p in correct) or '<p class="muted">None yet.</p>'}</details>
  <details class="list"><summary>Wrong ({len(wrong)})</summary>{''.join(result_row(p, money) for p in wrong) or '<p class="muted">None yet.</p>'}</details>
  {f'<details class="list"><summary>Pushes ({len(pushes)})</summary>' + ''.join(result_row(p, money) for p in pushes) + '</details>' if pushes else ''}
  <details class="list"><summary>Withdrawn before tip-off ({len(withdrawn)})</summary>
    <p class="muted small">Published earlier, then the line moved and the edge disappeared. Not counted in the record.</p>
    {''.join(f"<p>{escape(p['player'])} <span class='muted small'>{escape(p['game'])} · {_odds(p['odds'])}</span></p>" for p in withdrawn) or '<p class="muted">None.</p>'}
  </details>
</section>
<section>
  <h2>What the model is learning</h2>
  <div class="card">
    <p class="muted small">Every night the model projects every player with a prop in every game (all teams), and
    grades those projections the next morning. Once {betting.MIN_CALIBRATION_N} are graded it refits how much to
    trust itself vs the market, and only adopts a new fit if it predicts better on recent games it wasn't fitted on.</p>
    <table class="kv"><tbody>{learn_rows}</tbody></table>
    <h3 class="sub">Recalibration log</h3><ul class="results">{log_html}</ul>
  </div>
</section>"""
