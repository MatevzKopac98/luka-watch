"""Capture DraftKings game lines and player props (via ESPN) for every game on a US date.

Each run writes one snapshot file, so line movement through the morning is kept:
    data/daily/odds/<us_date>_<HHMM>.csv.gz    game lines: spread, total, moneyline (open + current)
    data/daily/props/<us_date>_<HHMM>.csv.gz   player props (open + current line and odds)

Props come in two shapes: over/under pairs ("Total Points": two rows, same line) and one-sided
ladders ("Points Milestones": 20+, 25+, ...). Rows are stored raw with their order inside the
feed (seq); the model works out which pair row is the over (see model code), so nothing is lost here.
"""
import csv
import gzip
import re
from pathlib import Path

from .http import get_json

CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
DATA = Path(__file__).resolve().parent.parent / "data" / "daily"

# Markets worth keeping for modelling (quarter props, first basket etc. are skipped)
KEEP = re.compile(r"^(Total (Points|Rebounds|Assists|3-Point Field Goals|Steals|Blocks|Points and Rebounds|"
                  r"Points and Assists|Assists and Rebounds|Points, Rebounds, and Assists)|"
                  r"(Points|Rebounds|Assists|3-Point Field Goals|Steals|Blocks|Points \+ Rebounds|Points \+ Assists|"
                  r"Rebounds \+ Assists|Points \+ Assists \+ Rebounds) Milestones|Team Total Points|"
                  r"To Record a (Double|Triple) Double)$")

ODDS_COLS = ["captured_at", "us_date", "event_id", "provider", "home_id", "away_id", "spread", "spread_open",
             "total", "total_open", "home_ml", "away_ml", "home_ml_open", "away_ml_open",
             "over_odds", "under_odds"]
PROP_COLS = ["captured_at", "us_date", "event_id", "seq", "athlete_id", "market", "line", "line_open",
             "odds", "odds_open", "decimal", "decimal_open", "last_updated"]


def _id(ref, kind):
    m = re.search(rf"/{kind}/(\d+)", ref or "")
    return m.group(1) if m else None


def _get(d, *path):
    for p in path:
        d = (d or {}).get(p) if isinstance(d, dict) else None
    return d


def game_lines(event_id, captured_at, us_date):
    data = get_json(f"{CORE}/events/{event_id}/competitions/{event_id}/odds")
    rows = []
    for ref in data.get("items", []):
        o = get_json(ref["$ref"]) if "$ref" in ref and len(ref) == 1 else ref
        home, away = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
        rows.append({
            "captured_at": captured_at, "us_date": us_date, "event_id": event_id,
            "provider": _get(o, "provider", "name"),
            "home_id": _id(_get(home, "team", "$ref"), "teams"), "away_id": _id(_get(away, "team", "$ref"), "teams"),
            # spread is from the home team's side; all odds stored as American prices
            "spread": o.get("spread"), "spread_open": _get(home, "open", "pointSpread", "american"),
            "total": o.get("overUnder"), "total_open": _get(o, "open", "total", "american"),
            "home_ml": home.get("moneyLine"), "away_ml": away.get("moneyLine"),
            "home_ml_open": _get(home, "open", "moneyLine", "american"),
            "away_ml_open": _get(away, "open", "moneyLine", "american"),
            "over_odds": o.get("overOdds"), "under_odds": o.get("underOdds"),
        })
    return rows


def props(event_id, captured_at, us_date, provider_id=100):
    url = f"{CORE}/events/{event_id}/competitions/{event_id}/odds/{provider_id}/propBets"
    rows, page = [], 1
    while True:
        data = get_json(url, {"limit": 1000, "page": page})
        for seq, x in enumerate(data.get("items", []), start=len(rows)):
            market = _get(x, "type", "name") or ""
            if not KEEP.match(market):
                continue
            rows.append({
                "captured_at": captured_at, "us_date": us_date, "event_id": event_id, "seq": seq,
                "athlete_id": _id(_get(x, "athlete", "$ref"), "athletes"), "market": market,
                "line": _get(x, "current", "target", "value"), "line_open": _get(x, "open", "target", "value"),
                "odds": _get(x, "odds", "american", "value"), "odds_open": _get(x, "odds", "american", "open"),
                "decimal": _get(x, "odds", "decimal", "value"), "decimal_open": _get(x, "odds", "decimal", "open"),
                "last_updated": x.get("lastUpdated"),
            })
        if page >= (data.get("pageCount") or 1):
            return rows
        page += 1


def _write(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def capture(us_date, captured_at, stamp):
    """Snapshot lines and props for every not-yet-started game on a US date ('YYYYMMDD')."""
    events = get_json(f"{SITE}/scoreboard", {"dates": us_date}).get("events", [])
    upcoming = [e["id"] for e in events if e["competitions"][0]["status"]["type"]["name"] == "STATUS_SCHEDULED"]
    odds_rows, prop_rows, failed = [], [], []
    for eid in upcoming:
        try:
            odds_rows += game_lines(eid, captured_at, us_date)
            prop_rows += props(eid, captured_at, us_date)
        except Exception as e:          # one game without lines shouldn't lose the rest
            failed.append(f"{eid}: {e}")
    if odds_rows:
        _write(DATA / "odds" / f"{us_date}_{stamp}.csv.gz", ODDS_COLS, odds_rows)
    if prop_rows:
        _write(DATA / "props" / f"{us_date}_{stamp}.csv.gz", PROP_COLS, prop_rows)
    return len(upcoming), len(odds_rows), len(prop_rows), failed
