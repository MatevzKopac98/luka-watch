"""Read captured DraftKings props into clean markets with fair (no-margin) probabilities.

Over/under props arrive as two rows with the same line and no side label; the first row in the feed
is the over (checked against the milestone ladders: 608 of 625 pairs agree, the rest are near-even
prices). Milestones ("25+ points") are one-sided ladders, kept as the market's view of the range.
"""
import csv
import gzip
from collections import defaultdict
from pathlib import Path

DAILY = Path(__file__).resolve().parent.parent / "data" / "daily"

# market name in the feed -> our stat key (combined stats are sums of box-score columns)
MARKETS = {
    "Total Points": "pts", "Total Rebounds": "reb", "Total Assists": "ast", "Total 3-Point Field Goals": "fg3m",
    "Total Points and Rebounds": "pts+reb", "Total Points and Assists": "pts+ast",
    "Total Assists and Rebounds": "reb+ast", "Total Points, Rebounds, and Assists": "pts+reb+ast",
}
MILESTONES = {
    "Points Milestones": "pts", "Rebounds Milestones": "reb", "Assists Milestones": "ast",
    "3-Point Field Goals Milestones": "fg3m", "Points + Rebounds Milestones": "pts+reb",
    "Points + Assists Milestones": "pts+ast", "Rebounds + Assists Milestones": "reb+ast",
    "Points + Assists + Rebounds Milestones": "pts+reb+ast",
}
STAT_LABEL = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes", "pts+reb": "points + rebounds",
              "pts+ast": "points + assists", "reb+ast": "rebounds + assists", "pts+reb+ast": "points + rebounds + assists"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def american_to_decimal(a):
    a = _f(a)
    if a is None or a == 0:
        return None
    return 1 + (a / 100 if a > 0 else 100 / -a)


def snapshots(us_date):
    """Snapshot files for a US date, oldest first."""
    return sorted((DAILY / "props").glob(f"{us_date}_*.csv.gz"))


def read_snapshot(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def markets(rows):
    """Main over/under lines: {(event_id, athlete_id, stat): {...}} with fair probabilities."""
    pairs = defaultdict(list)
    for r in rows:
        stat = MARKETS.get(r["market"])
        if stat and r["athlete_id"]:
            pairs[(r["event_id"], r["athlete_id"], stat, r["line"])].append(r)
    out = {}
    for (event, athlete, stat, line), v in pairs.items():
        if len(v) != 2:
            continue
        over, under = sorted(v, key=lambda r: int(r["seq"]))
        d_over, d_under = _f(over["decimal"]), _f(under["decimal"])
        if not d_over or not d_under:
            continue
        p_over, p_under = 1 / d_over, 1 / d_under
        out[(event, athlete, stat)] = {
            "event_id": event, "athlete_id": athlete, "stat": stat, "line": float(line),
            "line_open": _f(over["line_open"]),
            "over_odds": over["odds"], "under_odds": under["odds"], "over_dec": d_over, "under_dec": d_under,
            "fair_over": p_over / (p_over + p_under),            # margin removed proportionally
            "margin": p_over + p_under - 1,
            "captured_at": over["captured_at"],
        }
    return out


def ladders(rows):
    """Milestone ladders: {(event_id, athlete_id, stat): {threshold: implied probability}}."""
    out = defaultdict(dict)
    for r in rows:
        stat = MILESTONES.get(r["market"])
        d = _f(r["decimal"])
        if stat and r["athlete_id"] and d:
            out[(r["event_id"], r["athlete_id"], stat)][float(r["line"])] = 1 / d
    return dict(out)


def game_lines(us_date):
    """Latest and earliest captured game line per event: {event_id: {...}}."""
    files = sorted((DAILY / "odds").glob(f"{us_date}_*.csv.gz"))
    latest, first = {}, {}
    for path in files:
        for r in read_snapshot(path):
            first.setdefault(r["event_id"], r)
            latest[r["event_id"]] = r
    return {e: {**r, "first": first[e]} for e, r in latest.items()}
