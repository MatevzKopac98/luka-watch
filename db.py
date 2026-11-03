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

CREATE TABLE IF NOT EXISTS injuries (
    snapshot_date TEXT, team_id TEXT, player TEXT, status TEXT, detail TEXT,
    PRIMARY KEY (snapshot_date, team_id, player)
);
"""


def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def upsert(con, table, row):
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    con.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
