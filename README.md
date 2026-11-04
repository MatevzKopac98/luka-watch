# NBA Tracker — Luka, the MVP race & the Lakers

A daily-refreshed website tracking Luka Dončić against the league's best players,
the Lakers against the rest of the league, the Lakers' next opponent, and the MVP odds.
It also collects the data for a Lakers betting model (pilot, in progress).

## How it works

```
 06:41 Ljubljana (scheduled)                     odds capture, 4x per US game day
        │                                                  │
        ▼                                                  ▼
 ┌──────────────┐   ┌───────────────────────┐   ┌──────────────┐
 │  fetchers/   │──▶│ data/  ("data" branch) │──▶│  scripts/    │──▶  site/  ──▶  GitHub Pages
 │  ESPN,       │   │  nba.sqlite            │   │  build_site  │
 │  Polymarket  │   │  history/  daily/      │   │  model/      │
 └──────────────┘   └───────────────────────┘   └──────────────┘
```

1. **Fetch**: small Python modules, one per source.
2. **Store**: everything lives on a separate `data` branch that keeps only the latest snapshot
   (force-pushed every run), so the code branch stays small however much data piles up.
3. **Model**: `model/defense.py` rates every player's offense and defense from play-by-play lineups.
4. **Build & publish**: one static site, deployed to GitHub Pages by every run.

## Folders

| Folder | What lives there |
|---|---|
| `fetchers/` | `espn.py` (stats, schedule, box scores, injuries, rosters), `odds.py` (Polymarket MVP), `odds_capture.py` (DraftKings lines & props), `pbp.py` (play-by-play → lineup stints) |
| `model/` | `defense.py`: player offensive/defensive ratings and roster-projected team ratings |
| `scripts/` | `update.py` (daily run), `build_site.py` (pages), `capture_odds.py`, `backfill_history.py`, helpers |
| `data/` | *Checked out from the `data` branch.* `nba.sqlite` (site data), `history/<season>/` (every game: box scores + lineup stints, seasons 2024-25 onwards), `daily/odds` & `daily/props` (line snapshots) |
| `site/` | Generated website (not committed; built by every run) |
| `.github/workflows/` | `daily.yml` (morning update + deploy), `odds.yml` (odds capture through the US day) |

## Data sources

- **ESPN public API**: standings, team stats, schedule, player stats & game logs, box scores,
  play-by-play, injuries, rosters, news, and DraftKings game lines & player props
- **Polymarket**: MVP odds (implied probability = midpoint of bid/ask)

Sofascore and NBA.com block automated requests, so they aren't used.

## The lineup model

Play-by-play is cut into **stints** (stretches with the same ten players on court). ESPN skips most
between-quarter substitutions, so each quarter's starting five is inferred. Stint counts are calibrated
per team and game to the official box score. A ridge regression over all stints (last three seasons,
recent ones weighted more) gives each player an **offensive and defensive impact** per 100 possessions,
separated from teammates and opponents. Each team's **current healthy roster**, weighted by expected
minutes, then gives a projected offensive and defensive rating, blended with the team's real results as
games accumulate.

## Betting pilot (paper trading in EUR from a simulated €100, password protected)

- `model/props.py` reads captured DraftKings props: over/under pairs (first row = over) with the margin removed,
  and milestone ladders.
- `model/projections.py` projects each player: minutes (recent games, recently injured teammates, back-to-backs,
  blowout risk) x production per minute (recent > season > last season, usage boost) x matchup (opponent's
  roster-projected defence, pace), with a skewed (gamma) distribution for over/under probabilities.
- `model/betting.py` projects **every player with a prop in every game** (training data), picks Lakers bets
  where our probability beats the market's fair probability by 5+ points with +3% expected value, grades picks
  the next morning (result, closing line, why a miss happened) and recalibrates how much to trust the model
  vs the market once 400 projections are graded (only adopted if it predicts better on held-out games).
- The betting database and page are encrypted (AES-256-GCM, `model/crypto.py`) with the `BETTING_PASSWORD`
  GitHub Secret; without it the betting page shows "not configured".

## Running it

```bash
pip install -r requirements.txt
git worktree add data data                       # once: check out the data branch into data/
python scripts/update.py                         # fetch everything + rebuild site/
python scripts/capture_odds.py                   # snapshot tonight's lines and props
python scripts/backfill_history.py 2025 2026     # one-time history backfill (resumable)
python scripts/show_data.py                      # text summary of the latest snapshot
python scripts/end_times.py                      # when games ended, to tune the schedule
python scripts/check_sources.py                  # check every data source still responds
```

To get the latest data locally: `git -C data pull --rebase` won't work on a force-pushed branch, so use
`git -C data fetch && git -C data reset --hard origin/data`.

## Schedules

- **Daily update** (`daily.yml`): 06:41 (page ready by 07:00), with backup passes at 07:07, 07:37 and
  08:13 Ljubljana time (one hour later in summer time), because GitHub can delay or drop scheduled runs.
- **Odds capture** (`odds.yml`): 10:23, 14:23, 18:13 and 21:13 New York time, so closing lines are kept.
- Manual refresh: **Actions → Daily update → Run workflow**.

## Pages

- `index.html`: Luka, last & next Lakers game, MVP race, comparison table, Lakers vs. the league, milestones
- `last-night.html`: every game of the night with "why they won" insights, key player and box scores
