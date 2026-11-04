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


def pick_card(p):
    reasons = "".join(f"<li>{escape(r)}</li>" for r in json.loads(p["reasons"] or "[]"))
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
    <div><span class="lbl-k">Stake</span><strong>{p['units']:g}u</strong><span class="muted small">paper</span></div>
    <div><span class="lbl-k">Our chance</span><strong>{p['p_final'] * 100:.0f}%</strong>
      <span class="muted small">market {p['fair'] * 100:.0f}%</span></div>
    <div><span class="lbl-k">Edge</span><strong class="pos">{p['edge'] * 100:+.1f}</strong>
      <span class="muted small">EV {p['ev'] * 100:+.1f}%</span></div>
  </div>
  <p class="play">{play}</p>
  <p class="lbl-k">Why this bet</p><ul class="why">{reasons}</ul>
</article>"""


def result_row(p):
    reasons = "".join(f"<li>{escape(r)}</li>" for r in json.loads(p["reasons"] or "[]"))
    res = {"W": "W", "L": "L", "P": "P"}.get(p["result"], p["result"])
    profit = f"{p['profit']:+.2f}u" if p["profit"] is not None else ""
    clv = (f' · closing line {p["close_line"]:g} ({_odds(p["close_odds"])})' if p["close_line"] is not None else "")
    return f"""
<details class="res">
  <summary><span class="wl {res}">{res}</span> <strong>{escape(p['player'])}</strong>
    <span class="muted small">{escape(p['game'])} · {_local(p['tip_utc'], '%d %b')} · {_odds(p['odds'])} · {p['units']:g}u</span>
    <span class="res-profit {'pos' if (p['profit'] or 0) > 0 else 'neg' if (p['profit'] or 0) < 0 else ''}">{profit}</span></summary>
  <p><span class="tag">{CATEGORY.get(p['miss_category'], p['miss_category'] or '')}</span> {escape(p['miss_text'] or '')}</p>
  <p class="muted small">Our chance {p['p_final'] * 100:.0f}% vs market {p['fair'] * 100:.0f}%{clv}</p>
  <p class="lbl-k">Reasoning at the time</p><ul class="why">{reasons}</ul>
</details>"""


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

    picks_html = "".join(pick_card(p) for p in active) or (
        '<div class="card"><p>No picks right now. Either tonight\'s Lakers props aren\'t posted yet '
        '(DraftKings usually posts them on game day; lines are checked at 16:23, 20:23, 00:13 and 03:13), '
        'or nothing clears the bar: our probability must beat the market by 5+ points with +3% expected value.</p></div>')
    misses = " · ".join(f"{CATEGORY.get(k, k)} {v}" for k, v in sorted(rec["misses"].items(), key=lambda x: -x[1])) or "–"
    tiles = [("Record", f"{rec['w']}-{rec['l']}" + (f"-{rec['p']}" if rec["p"] else ""), f"hit rate {hit}"),
             ("Profit", f"{rec['units']:+.2f}u", f"ROI {rec['roi']:+.1f}%" if rec["roi"] is not None else "ROI –"),
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
  bet, no real money, until there are enough graded picks to judge the model. 1u = 1% of a bankroll.</p>
</section>
<section>
  <h2>Tonight's picks</h2>
  <div class="bets">{picks_html}</div>
</section>
<section>
  <h2>Track record</h2>
  <div class="tiles">{tiles_html}</div>
  <details class="list"><summary>Correct ({len(correct)})</summary>{''.join(result_row(p) for p in correct) or '<p class="muted">None yet.</p>'}</details>
  <details class="list"><summary>Wrong ({len(wrong)})</summary>{''.join(result_row(p) for p in wrong) or '<p class="muted">None yet.</p>'}</details>
  {f'<details class="list"><summary>Pushes ({len(pushes)})</summary>' + ''.join(result_row(p) for p in pushes) + '</details>' if pushes else ''}
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
