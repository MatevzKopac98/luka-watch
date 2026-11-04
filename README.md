# NBA Tracker — Luka, the MVP race & the Lakers

A daily-refreshed website tracking Luka Dončić against the league's best players,
the Lakers against the rest of the league, the Lakers' next opponent, and the MVP odds.

## How it works

```
 06:50 CET (scheduled)
        │
        ▼
 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │  fetchers/   │──▶│   data/      │──▶│  scripts/    │──▶  site/index.html  ──▶  public link
 │  pull stats, │   │  nba.sqlite  │   │  build_site  │      (static page)
 │  odds, sched │   │  (history)   │   │  (render)    │
 └──────────────┘   └──────────────┘   └──────────────┘
```

1. **Fetch** — small Python modules, one per source, each writes into the database.
2. **Store** — one SQLite file keeps every day's snapshot, so we can draw trends
   (e.g. how Luka's MVP odds moved over the season).
3. **Build** — a script reads the database and renders one static HTML page.
4. **Publish** — the page is pushed to a free host so anyone with the link can open it.

## Folders

| Folder | What lives there |
|---|---|
| `fetchers/` | One module per data source (player stats, team stats, schedule, MVP odds) |
| `data/` | `nba.sqlite` — the database (daily snapshots) |
| `scripts/` | `update.py` (runs everything), `build_site.py` (renders the page) |
| `site/` | The generated website (`index.html` + `assets/`) — this is what gets published |
| `.github/workflows/` | Optional cloud scheduler (GitHub Actions) for the 7:00 refresh |

## Data sources

- **ESPN public API**: standings, team stats, Lakers schedule, player stats & game logs, injuries
- **Polymarket**: MVP odds (implied probability = midpoint of bid/ask)

Sofascore and NBA.com block automated requests, so they aren't used.

## Running it

```bash
pip install -r requirements.txt
python scripts/update.py        # fetch everything + rebuild site/index.html
python scripts/show_data.py     # text summary of the latest snapshot
python scripts/check_sources.py # check every data source still responds
```

## Daily schedule

GitHub Actions (`.github/workflows/daily.yml`) runs the update at 06:41 (page ready by 07:00), with
backup passes at 07:07, 07:37 and 08:13 Ljubljana time (one hour later during summer time), because
GitHub can delay or drop scheduled runs. Each run commits the database and publishes `site/` to GitHub Pages. `python scripts/end_times.py` shows when games actually ended each
night, to tune those times.

## Pages

- `index.html`: Luka, last & next Lakers game, MVP race, comparison table, Lakers vs. the league
- `last-night.html`: every game of the night with "why they won" insights, key player and box scores
Use **Actions → Daily update → Run workflow** for a manual refresh.
To get the latest database locally: `git pull`.
