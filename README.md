# NBA Tracker — Luka, the MVP race & the Lakers

A daily-refreshed website tracking Luka Dončić against the league's best players,
the Lakers against the rest of the league, the Lakers' next opponent, and the MVP odds.

## How it works

```
 06:41 Ljubljana (scheduled)
        │
        ▼
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
| `fetchers/` | `espn.py` (stats, schedule, box scores, injuries, rosters), `odds.py` (Polymarket MVP), `pbp.py` (play-by-play → lineup stints) |
| `model/` | `defense.py`: player offensive/defensive ratings and roster-projected team ratings |
| `scripts/` | `update.py` (daily run), `build_site.py` (pages), `backfill_history.py`, helpers |
| `data/` | *Checked out from the `data` branch.* `nba.sqlite` (site data), `history/<season>/` (every game: box scores + lineup stints, seasons 2024-25 onwards) |
| `site/` | Generated website (not committed; built by every run) |
| `.github/workflows/` | `daily.yml` (morning update + deploy), `backup.yml` (weekly backup), `tests.yml` |

## Data sources

- **ESPN public API**: standings, team stats, schedule, player stats & game logs, box scores,
  play-by-play, injuries, rosters, news
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

## Running it

```bash
pip install -r requirements.txt
git worktree add data data                       # once: check out the data branch into data/
python scripts/update.py                         # fetch everything + rebuild site/
python scripts/backfill_history.py 2025 2026     # one-time history backfill (resumable)
python scripts/show_data.py                      # text summary of the latest snapshot
python scripts/end_times.py                      # when games ended, to tune the schedule
python scripts/check_sources.py                  # check every data source still responds
pip install -r requirements-dev.txt && python -m pytest -q   # offline tests
```

To get the latest data locally: `git -C data pull --rebase` won't work on a force-pushed branch, so use
`git -C data fetch && git -C data reset --hard origin/data`.

## Schedules

- **Daily update** (`daily.yml`): 06:41 (page ready by 07:00), with backup passes at 07:07, 07:37 and
  08:13 Ljubljana time (one hour later in summer time), because GitHub can delay or drop scheduled runs.
- **External timer** (cron-job.org, job "Luka Watch morning update"): starts the daily update at exactly 06:41
  Ljubljana time via GitHub's workflow_dispatch API, because GitHub's own schedule runs 40-60 minutes late or skips
  runs. It uses a fine-grained token limited to Actions on this repo, expiring 3 Nov 2027 (renew it in GitHub,
  then update the Authorization header in the job). The GitHub schedules above remain as a backup.
- **Weekly backup** (`backup.yml`): Mondays 05:17, saves the site database
  as a `backup-YYYY-MM-DD` GitHub release (last 8 kept), because the data branch keeps no history.
- **Tests** (`tests.yml`): on every code push; they don't block the daily update.
- Manual refresh: **Actions → Daily update → Run workflow**.

## Salaries (Basketball-Reference export, refreshed by hand)

ESPN misses many new contracts, so team payrolls come from the Basketball-Reference contract tables, exported
by hand (no scraping) once a month:

1. On [players](https://www.basketball-reference.com/contracts/players.html) and
   [teams](https://www.basketball-reference.com/contracts/) choose **Share & Export → Get table as Excel**.
2. `python scripts/import_contracts.py` picks the newest two exports from Downloads, checks that every team's
   player salaries add up to its payroll, lists players without a contract (mostly two-way deals) and waived
   players still being paid, and writes `contracts/contracts.json` (plus `contracts/report.txt`).
3. Commit `contracts/` and push. Team pages credit the source and ask for a new export after a month.

## Health checks

Each morning run checks every source (30 teams in the standings, MVP odds, injuries, last night's box scores)
and saves the result in `data/status.json`. Every page shows a red notice when
a source failed or when the last morning update is more than 26 hours old (checked in the reader's browser, so it
also appears when no run happened at all), and the footer lists any issues.

## Pages

- `index.html`: Luka, last & next Lakers game, MVP race, comparison table, Lakers vs. the league, milestones
- `last-night.html`: every game of the night with "why they won" insights, key player and box scores
- `nights/<YYYYMMDD>.html`: the same for every game night of the season (date menu and previous/next on each page)
- `teams/<ABBR>.html`: one page per team (open from the Teams tab or the standings): record and ratings, players
  (this/last season, per game/per 36/totals, salary), five-man lineups (most used/best), rotation chart with usage,
  impact, value for money and contracts

## Second address (Cloudflare Pages)

Work networks that block github.io as "file sharing" (e.g. WithSecure) can use **https://luka-watch.pages.dev**.
Every run that publishes to GitHub Pages also uploads the same `site/` folder to Cloudflare Pages
(last step of `daily.yml`). It needs two repository secrets:

- `CLOUDFLARE_ACCOUNT_ID`: Cloudflare dashboard → Workers & Pages → Account ID (right-hand side)
- `CLOUDFLARE_API_TOKEN`: My Profile → API Tokens → Create token → Custom token, permission
  *Account → Cloudflare Pages → Edit*

Without them the step is skipped; a Cloudflare error never fails the run. The github.io address keeps working.

The token `luka-watch` (Cloudflare Pages: Edit) **expires 11 Nov 2027**. Before then, create a new one the same
way and replace the `CLOUDFLARE_API_TOKEN` secret (good moment: together with the cron-job token, which expires 3 Nov 2027).
