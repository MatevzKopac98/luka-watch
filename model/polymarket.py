"""Polymarket prices for the same bets as our picks (public data, no account needed).

Polymarket prices are probabilities: buying the over at 0.45 pays 1/0.45 = 2.22x if it wins.
Only markets with real money behind them are used; thin ones ($ few in liquidity) have meaningless prices.
"""
import json
import re

from fetchers.http import get_json

GAMMA = "https://gamma-api.polymarket.com"
MIN_LIQUIDITY = 300          # dollars
MAX_SPREAD = 0.06            # bid/ask gap; wider = price not reliable
STAT_NAMES = {"pts": "Points", "reb": "Rebounds", "ast": "Assists", "fg3m": "3-Pointers Made"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def game_event(us_date, opp_nickname):
    """The Polymarket event for tonight's Lakers game, or None."""
    day = f"{us_date[:4]}-{us_date[4:6]}-{us_date[6:]}"
    data = get_json(f"{GAMMA}/public-search", {"q": f"Lakers {opp_nickname}"})
    for e in data.get("events", []):
        if e.get("slug", "").startswith("nba-") and e["slug"].endswith(day) and not e.get("closed"):
            full = get_json(f"{GAMMA}/events", {"slug": e["slug"]})
            return full[0] if full else None
    return None


def _prices(m):
    """(first-outcome buy price, second-outcome buy price) if the market is liquid enough."""
    bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))
    if not bid or not ask or ask - bid > MAX_SPREAD or (_f(m.get("liquidityNum")) or 0) < MIN_LIQUIDITY:
        return None
    if m.get("closed") or not m.get("active", True):
        return None
    return ask, 1 - bid


def find(event, pick):
    """Matching market for a pick: {line, price, dec, liquidity, label} or None.

    Exact line preferred; otherwise the nearest line within 1 point (shown as a different line).
    """
    if not event:
        return None
    best = None
    for m in event.get("markets", []):
        q = m.get("question", "")
        outcomes = json.loads(m.get("outcomes") or "[]")
        side_index, line = None, None
        if pick["kind"] == "prop":
            stat = STAT_NAMES.get(pick["stat"])
            mm = re.match(rf"^(.+?): {re.escape(stat or '#')} O/U ([\d.]+)$", q)
            if not stat or not mm or not _same_player(mm.group(1), pick["player_name"]):
                continue
            line, side_index = float(mm.group(2)), 0 if pick["side"] == "over" else 1
        elif pick["kind"] == "total":
            mm = re.match(r"^.+?: O/U ([\d.]+)$", q)
            if not mm or q.startswith(("1H", "1Q", "2Q", "3Q", "4Q")):
                continue
            line, side_index = float(mm.group(1)), 0 if pick["side"] == "over" else 1
        elif pick["kind"] == "spread":
            mm = re.match(r"^Spread: (.+?) \((-?[\d.]+)\)$", q)
            if not mm or not outcomes:
                continue
            favourite, fav_line = mm.group(1), float(mm.group(2))
            lakers_line = fav_line if favourite == "Lakers" else -fav_line
            line = lakers_line if pick["side"] == "lakers" else -lakers_line
            lakers_index = outcomes.index("Lakers") if "Lakers" in outcomes else 0
            side_index = lakers_index if pick["side"] == "lakers" else 1 - lakers_index
        if line is None or abs(line - pick["line"]) > 1:
            continue
        prices = _prices(m)
        if not prices:
            continue
        price = prices[side_index]
        cand = {"line": line, "price": round(price, 3), "dec": round(1 / price, 2) if price > 0 else None,
                "liquidity": round(_f(m.get("liquidityNum")) or 0), "question": q}
        if best is None or abs(line - pick["line"]) < abs(best["line"] - pick["line"]):
            best = cand
    return best


def _same_player(a, b):
    norm = lambda s: re.sub(r"[^a-z]", "", s.lower())
    return norm(a) == norm(b)
