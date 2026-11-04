"""One-time backfill of complete past seasons: box scores and play-by-play stints for every game.

Run:  python scripts/backfill_history.py 2025 2026      (ESPN season years: 2025 = 2024-25)

Writes per season, under data/history/<season>/:
    games.csv.gz      score, quarters, teams              players.csv.gz   every player's box score
    team_box.csv.gz   team box scores                     stints.csv.gz    lineups on court + what happened
Resumable: finished games are listed in _done.txt and skipped on a re-run.
"""
import csv
import gzip
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fetchers import espn                     # noqa: E402
from fetchers.http import get_json            # noqa: E402
from fetchers.pbp import build_stints         # noqa: E402

HISTORY = Path(__file__).resolve().parent.parent / "data" / "history"
PAUSE = 0.15          # seconds between requests, to be polite to ESPN


def _columns(path, rows):
    """A file's columns: its existing header, else the first rows' keys plus every known field."""
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as f:
            header = f.readline().strip()
        if header:
            return header.split(",")
    cols = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    known = {"team_box": [v for x in espn.TEAM_BOX.values() for v in (x if isinstance(x, tuple) else (x,))],
             "players": [v for x in espn.PLAYER_BOX.values() for v in (x if isinstance(x, tuple) else (x,))]}
    return cols + [k for k in known.get(path.name.split(".")[0], []) if k not in cols]


def append_rows(path, rows):
    """Append rows to a gzipped CSV under a fixed header; fields a game lacks are left empty."""
    if not rows:
        return
    new = not path.exists()
    cols = _columns(path, rows)
    with gzip.open(path, "at", newline="", encoding="utf-8") as f:     # gzip members can be appended
        w = csv.DictWriter(f, fieldnames=cols, restval="", extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def repair(out, done):
    """After an interrupted run: keep only complete rows of games marked done (drops partial writes)."""
    for name in ("games", "team_box", "players", "stints"):
        path = out / f"{name}.csv.gz"
        if not path.exists():
            continue
        rows = []
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    rows.append(r)
        except (EOFError, OSError, csv.Error):
            pass                                              # truncated tail: keep what was read
        # a truncated row shows up with missing values (None) or overflow fields (key None)
        keep = [r for r in rows if r.get("event_id") in done and None not in r and None not in r.values()]
        path.unlink()                                         # rewrite as one clean gzip member
        append_rows(path, keep)


def season_dates(season):
    """Every calendar day that can hold regular-season games (filtered by season type later)."""
    d, end = date(season - 1, 10, 1), min(date(season, 4, 30), date.today() - timedelta(days=1))
    while d <= end:
        yield d
        d += timedelta(days=1)


def backfill(season):
    out = HISTORY / str(season)
    out.mkdir(parents=True, exist_ok=True)
    done_file = out / "_done.txt"
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    if done:
        repair(out, done)
    low_quality = 0
    for day in season_dates(season):
        us_date = day.strftime("%Y%m%d")
        events = get_json(f"{espn.SITE}/scoreboard", {"dates": us_date}).get("events", [])
        time.sleep(PAUSE)
        games = [e for e in events if (e.get("season") or {}).get("type") == 2
                 and e["competitions"][0]["status"]["type"]["name"] == "STATUS_FINAL" and e["id"] not in done]
        for e in games:
            try:
                s = espn.raw_summary(e["id"])
                info, teams, players = espn.game_summary(e["id"], us_date, s)
                stints, quality = build_stints(s)
            except Exception as ex:
                print(f"  {us_date} {e['id']}: FAILED {ex}", flush=True)
                continue
            info["season"], info["lineup_quality"] = season, round(quality, 3)
            low_quality += quality < 0.95
            append_rows(out / "games.csv.gz", [info])
            append_rows(out / "team_box.csv.gz", teams)
            append_rows(out / "players.csv.gz", players)
            append_rows(out / "stints.csv.gz", stints)
            with open(done_file, "a") as f:
                f.write(e["id"] + "\n")
            done.add(e["id"])
            time.sleep(PAUSE)
        if games:
            print(f"{season} {us_date}: +{len(games)} games (total {len(done)}, low-quality lineups {low_quality})",
                  flush=True)
    print(f"Season {season} complete: {len(done)} games, {low_quality} with lineup quality < 95%", flush=True)


if __name__ == "__main__":
    for s in [int(a) for a in sys.argv[1:]] or [2025, 2026]:
        backfill(s)
