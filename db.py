"""SQLite database: schema and connection. Season tables are daily snapshots, so trends can be charted."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "nba.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    espn_id   TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    team_abbr TEXT,
    team_id   TEXT,
    position  TEXT,
    headshot  TEXT,
    tracked   INTEGER DEFAULT 0          -- 1 = always shown (core comparison group)
);

CREATE TABLE IF NOT EXISTS player_season (           -- one row per player per day
    snapshot_date TEXT, espn_id TEXT,
    gp INTEGER, min REAL, pts REAL, reb REAL, ast REAL, stl REAL, blk REAL, tov REAL,
    fgm REAL, fga REAL, fg_pct REAL, fg3m REAL, fg3a REAL, fg3_pct REAL,
    ftm REAL, fta REAL, ft_pct REAL, ts_pct REAL, dd2 INTEGER, td3 INTEGER,
    PRIMARY KEY (snapshot_date, espn_id)
);

CREATE TABLE IF NOT EXISTS player_games (            -- game log, one row per game
    espn_id TEXT, event_id TEXT, game_date TEXT, opp_abbr TEXT, home_away TEXT,
    result TEXT, score TEXT,
    min REAL, pts INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER,
    fgm INTEGER, fga INTEGER, fg3m INTEGER, fg3a INTEGER, ftm INTEGER, fta INTEGER,
    PRIMARY KEY (espn_id, event_id)
);

CREATE TABLE IF NOT EXISTS team_season (             -- one row per team per day
    snapshot_date TEXT, team_id TEXT, abbr TEXT, name TEXT, conference TEXT, logo TEXT,
    wins INTEGER, losses INTEGER, win_pct REAL, seed INTEGER, games_behind TEXT,
    streak TEXT, last10 TEXT, home TEXT, road TEXT,
    ppg REAL, opp_ppg REAL, diff REAL,
    fg_pct REAL, fg3_pct REAL, ft_pct REAL, reb REAL, oreb REAL, ast REAL, tov REAL, stl REAL, blk REAL,
    pace REAL, ortg REAL, drtg REAL, net_rtg REAL,
    PRIMARY KEY (snapshot_date, team_id)
);

CREATE TABLE IF NOT EXISTS games (                   -- Lakers schedule & results
    event_id TEXT PRIMARY KEY, date_utc TEXT, status TEXT,
    home_id TEXT, home_abbr TEXT, home_score INTEGER,
    away_id TEXT, away_abbr TEXT, away_score INTEGER
);

CREATE TABLE IF NOT EXISTS mvp_odds (                -- implied probability per source per day
    snapshot_date TEXT, source TEXT, player_name TEXT,
    prob REAL, bid REAL, ask REAL,
    PRIMARY KEY (snapshot_date, source, player_name)
);

CREATE TABLE IF NOT EXISTS nights_done (             -- US dates whose box scores are all stored
    us_date TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS luka_vs_opp (              -- Luka's per-opponent averages, every season
    season INTEGER, opp_abbr TEXT, gp REAL, min REAL, pts REAL, reb REAL, ast REAL,
    fgm REAL, fga REAL, fg3m REAL, fg3a REAL, ftm REAL, fta REAL,
    PRIMARY KEY (season, opp_abbr)
);

CREATE TABLE IF NOT EXISTS luka_prev_games (          -- Luka's game log from last season
    season INTEGER, espn_id TEXT, event_id TEXT, game_date TEXT, opp_abbr TEXT, home_away TEXT,
    result TEXT, score TEXT,
    min REAL, pts INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER,
    fgm INTEGER, fga INTEGER, fg3m INTEGER, fg3a INTEGER, ftm INTEGER, fta INTEGER,
    PRIMARY KEY (espn_id, event_id)
);

CREATE TABLE IF NOT EXISTS luka_news (                -- record/history headlines, kept as they appear
    url TEXT PRIMARY KEY, published TEXT, headline TEXT
);

CREATE TABLE IF NOT EXISTS player_career (            -- career regular-season totals (milestones)
    snapshot_date TEXT, espn_id TEXT,
    gp REAL, pts REAL, reb REAL, ast REAL, fg3m REAL, stl REAL, blk REAL, dd2 REAL, td3 REAL,
    PRIMARY KEY (snapshot_date, espn_id)
);

CREATE TABLE IF NOT EXISTS league_players (          -- every qualified player, for league ranks
    snapshot_date TEXT, espn_id TEXT, name TEXT, team_abbr TEXT,
    gp REAL, min REAL, pts REAL, reb REAL, ast REAL, stl REAL, blk REAL, tov REAL,
    fga REAL, fg_pct REAL, fg3m REAL, fg3a REAL, fg3_pct REAL, fta REAL, ft_pct REAL, ts_pct REAL,
    dd2 REAL, td3 REAL,
    PRIMARY KEY (snapshot_date, espn_id)
);

CREATE TABLE IF NOT EXISTS game_info (               -- every finished game: score, quarters, end time
    event_id TEXT PRIMARY KEY, us_date TEXT, start_utc TEXT, end_utc TEXT, status TEXT,
    home_id TEXT, home_abbr TEXT, home_score INTEGER, home_lines TEXT,
    away_id TEXT, away_abbr TEXT, away_score INTEGER, away_lines TEXT,
    headline TEXT, highlights_url TEXT
);

CREATE TABLE IF NOT EXISTS game_team_box (
    event_id TEXT, team_id TEXT, abbr TEXT,
    fgm REAL, fga REAL, fg3m REAL, fg3a REAL, ftm REAL, fta REAL,
    reb REAL, oreb REAL, dreb REAL, ast REAL, stl REAL, blk REAL, tov REAL, pf REAL,
    pts_off_tov REAL, fastbreak REAL, paint REAL, largest_lead REAL, lead_changes REAL, lead_pct REAL,
    PRIMARY KEY (event_id, team_id)
);

CREATE TABLE IF NOT EXISTS game_players (
    event_id TEXT, team_id TEXT, athlete_id TEXT, name TEXT, starter INTEGER,
    min REAL, pts REAL, fgm REAL, fga REAL, fg3m REAL, fg3a REAL, ftm REAL, fta REAL,
    reb REAL, oreb REAL, dreb REAL, ast REAL, tov REAL, stl REAL, blk REAL, pf REAL, plus_minus REAL,
    PRIMARY KEY (event_id, athlete_id)
);

CREATE TABLE IF NOT EXISTS injuries (
    snapshot_date TEXT, team_id TEXT, player TEXT, status TEXT, detail TEXT, athlete_id TEXT,
    PRIMARY KEY (snapshot_date, team_id, player)
);

CREATE TABLE IF NOT EXISTS player_defense (           -- from play-by-play stints, last 3 seasons weighted
    athlete_id TEXT PRIMARY KEY, name TEXT, team_id TEXT,
    def_impact REAL, off_impact REAL, on_drtg REAL, on_ortg REAL, on_off REAL, def_poss REAL
);

CREATE TABLE IF NOT EXISTS team_projection (          -- roster-projected vs actual offense and defense
    snapshot_date TEXT, team_id TEXT, league_rtg REAL, last_season_ortg REAL, last_season_drtg REAL,
    roster_ortg REAL, roster_drtg REAL, actual_ortg REAL, actual_drtg REAL, gp INTEGER,
    blend_ortg REAL, blend_drtg REAL,
    PRIMARY KEY (snapshot_date, team_id)
);
"""

MIGRATIONS = [   # columns added after a table first shipped (CREATE IF NOT EXISTS won't add them)
    ("injuries", "athlete_id", "TEXT"),
]


def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for table, column, kind in MIGRATIONS:
        if column not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    return con


def upsert(con, table, row):
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    con.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
