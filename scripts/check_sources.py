"""Health check: confirms every data source still responds. Run: python scripts/check_sources.py"""
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
     "Accept": "application/json"}
URLS = {
    "espn_standings": "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings",
    "espn_lal_sched": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/13/schedule",
    "espn_lal_stats": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/13/statistics",
    "espn_luka_stats": "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/3945274/stats",
    "espn_luka_gamelog": "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/3945274/gamelog",
    "espn_injuries": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",
    "poly_mvp": "https://gamma-api.polymarket.com/public-search?q=NBA%20MVP",
    "sofascore": "https://api.sofascore.com/api/v1/team/3427/events/next/0",
    "nba_stats": "https://stats.nba.com/stats/leaguedashplayerstats?Season=2026-27&SeasonType=Regular%20Season"
                 "&PerMode=PerGame&MeasureType=Base&LeagueID=00&LastNGames=0&Month=0&OpponentTeamID=0"
                 "&PaceAdjust=N&Period=0&PlusMinus=N&Rank=N&TeamID=0",
}

for name, url in URLS.items():
    headers = dict(H)
    if name == "nba_stats":
        headers.update({"Referer": "https://www.nba.com/", "Origin": "https://www.nba.com",
                        "x-nba-stats-origin": "stats", "x-nba-stats-token": "true"})
    try:
        r = requests.get(url, headers=headers, timeout=20)
        print(f"== {name}: {r.status_code} len={len(r.text)}")
        print(r.text[:300].replace("\n", " "))
    except Exception as e:
        print(f"== {name}: ERR {e}")
