"""MVP odds from Polymarket (read-only public data). Prices are implied probabilities (0-1)."""
import json
from .http import get_json

POLYMARKET = "https://gamma-api.polymarket.com/events"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def polymarket_mvp(slug):
    events = get_json(POLYMARKET, {"slug": slug})
    if not events:
        return []
    out = []
    for m in events[0].get("markets", []):
        name = m.get("groupItemTitle") or ""
        bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))
        if m.get("closed") or not bid or name.startswith("Player "):   # skip unlisted placeholders
            continue
        # Mid of bid/ask is a fairer read than a stale last trade
        prob = (bid + ask) / 2 if ask else _f(json.loads(m.get("outcomePrices") or "[]")[0])
        out.append({"source": "Polymarket", "player_name": name, "prob": round(prob, 4), "bid": bid, "ask": ask})
    return out
