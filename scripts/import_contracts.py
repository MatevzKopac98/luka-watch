"""Import the contract tables you export from Basketball-Reference and check them.

1. On https://www.basketball-reference.com/contracts/players.html and https://www.basketball-reference.com/contracts/
   use "Share & Export" > "Get table as Excel" (files land in Downloads as sportsref_download*.xls).
2. Run: python scripts/import_contracts.py            (picks the newest two exports from Downloads)
   or:  python scripts/import_contracts.py PLAYERS.xls TEAMS.xls
3. Commit contracts/ and push; the daily update reads it from there.

The report checks that each team's player salaries add up to its payroll, lists roster players without a
contract (usually two-way deals, which don't count against the cap) and contracts of players no longer on
an ESPN roster (waived players the team still pays).
"""
import json
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fetchers import bbref_contracts as bb   # noqa: E402
from fetchers.espn import _plain              # noqa: E402

DIR = ROOT / "contracts"
PLAYERS_FILE, TEAMS_FILE, SUMMARY_FILE = DIR / "players.xls", DIR / "teams.xls", DIR / "contracts.json"
BBREF_TO_ESPN = {"BRK": "BKN", "CHO": "CHA", "GSW": "GS", "NOP": "NO", "NYK": "NY", "PHO": "PHX", "SAS": "SA",
                 "UTA": "UTAH", "WAS": "WSH"}
TEAM_NAME_FIX = {"Los Angeles Clippers": "LA Clippers"}
REMIND_DAYS = 31             # the site asks for fresh exports after this many days (monthly refresh)


def name_key(n):
    return " ".join(re.sub(r"\b(jr|sr|ii|iii|iv)\b|[^a-z ]", "", _plain(n or "").lower()).split())


def kind(path):
    head = Path(path).read_text(encoding="utf-8", errors="replace")[:6000]
    return "players" if 'data-stat="player"' in head else "teams" if 'data-stat="team_name"' in head else None


def newest_exports():
    files = sorted(Path.home().joinpath("Downloads").glob("sportsref_download*.xls"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    found = {}
    for f in files:
        k = kind(f)
        if k and k not in found:
            found[k] = f
    return found.get("players"), found.get("teams")


def build(players_path, teams_path, espn_teams):
    """Per-team costs for this season, reconciled with the team payroll table.

    espn_teams: {ESPN abbr: full name}. Returns the summary saved to contracts/contracts.json.
    """
    rows, seasons = bb.players(players_path)
    team_rows, _ = bb.teams(teams_path)
    season = seasons[0]
    abbr_of_name = {v: k for k, v in espn_teams.items()}
    payroll = {}
    for t in team_rows:
        a = abbr_of_name.get(TEAM_NAME_FIX.get(t["team"], t["team"]))
        if a:
            payroll[a] = {s: v for s, v in t["salaries"].items()}
    # One row per player and team (the export repeats some rows)
    seen, unique = set(), []
    for r in rows:
        r = {**r, "team": BBREF_TO_ESPN.get(r["team"], r["team"])}
        k = (r["bbref_id"] or r["name"], r["team"])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    # Waived players appear on both teams with the full salary; their cost to each team is what's left of the
    # team's payroll once everyone else is counted (exact when a team has one such player, else split by guarantee)
    count = defaultdict(int)
    for r in unique:
        count[r["bbref_id"] or r["name"]] += 1
    for r in unique:
        r["cost"] = r["salaries"].get(season, 0)
        r["split"] = count[r["bbref_id"] or r["name"]] > 1
    for team, pay in payroll.items():
        mine = [r for r in unique if r["team"] == team]
        split = [r for r in mine if r["split"]]
        if not split:
            continue
        residual = pay.get(season, 0) - sum(r["cost"] for r in mine if not r["split"])
        weights = [r["guaranteed"] or 1 for r in split]
        for r, w in zip(split, weights):
            r["cost"] = round(residual * w / sum(weights)) if len(split) > 1 else residual
    checks = []
    for team, pay in sorted(payroll.items()):
        total = sum(r["cost"] for r in unique if r["team"] == team)
        checks.append({"team": team, "payroll": pay.get(season, 0), "players_sum": total,
                       "diff": total - pay.get(season, 0)})
    return {"season": season, "seasons": seasons, "exported": date.today().isoformat(),
            "players": [{"name": r["name"], "key": name_key(r["name"]), "bbref_id": r["bbref_id"], "team": r["team"],
                         "cost": r["cost"], "salaries": r["salaries"], "guaranteed": r["guaranteed"],
                         "waived_split": r["split"]} for r in unique],
            "payroll": payroll, "checks": checks}


def match(summary, roster):
    """Pair ESPN roster players with exported contracts.

    roster: [{name, abbr, ...}]. Returns {index in roster: (status, contract or None)}: "ok" (contract with his
    current team), "elsewhere" (only a contract with another team: waived or traded, new deal not in the export)
    or "none" (no contract: usually a two-way deal). Names match without accents and suffixes, then by
    team + last name + first initial (Ron / Ronald Holland).
    """
    by_key, by_last = defaultdict(list), defaultdict(list)
    for c in summary["players"]:
        by_key[c["key"]].append(c)
        parts = c["key"].split()
        if parts:
            by_last[(c["team"], parts[-1], parts[0][:1])].append(c)
    out = {}
    for i, r in enumerate(roster):
        k = name_key(r["name"])
        cands = by_key.get(k, [])
        mine = [c for c in cands if c["team"] == r["abbr"]]
        if not mine and k:
            parts = k.split()
            mine = by_last.get((r["abbr"], parts[-1], parts[0][:1]), [])
        out[i] = ("ok", mine[0]) if mine else ("elsewhere", cands[0]) if cands else ("none", None)
    return out


def report(summary, roster, espn_teams):
    """Plain-text report: team sums, roster players with no contract, contracts not on a roster."""
    lines = [f"Season {summary['season']}: {len(summary['players'])} contracts, {len(summary['payroll'])} teams"]
    bad = [c for c in summary["checks"] if abs(c["diff"]) > 1]
    if len(summary["payroll"]) < 30:
        lines.append(f"!! Team table has only {len(summary['payroll'])} of 30 teams")
    lines.append("Team sums match the team payroll table for all teams." if not bad else
                 "Team sums that DON'T match: " + ", ".join(f"{c['team']} {c['diff'] / 1e6:+.2f}M" for c in bad))
    split = [p for p in summary["players"] if p["waived_split"]]
    if split:
        lines.append("Waived and signed elsewhere (cost split between teams): "
                     + ", ".join(f"{p['name']} {p['team']} ${p['cost'] / 1e6:.1f}M" for p in split))
    m = match(summary, roster)
    moved = [(r, m[i][1]) for i, r in enumerate(roster) if m[i][0] == "elsewhere"]
    lines.append(f"\nOn a new team, contract still listed with the old one: {len(moved)}"
                 " (waived or traded; the new deal isn't in the export yet)")
    for r, c in sorted(moved, key=lambda x: x[0]["abbr"]):
        lines.append(f"  {r['name']}: now {r['abbr']}, contract with {c['team']} (${c['cost'] / 1e6:.2f}M)")
    missing = [r for i, r in enumerate(roster) if m[i][0] == "none"]
    lines.append(f"\nRoster players with no contract in the export: {len(missing)} of {len(roster)}"
                 " (almost all two-way contracts, which don't count toward payroll)")
    for team in sorted({r["abbr"] for r in missing}):
        lines.append(f"  {team}: " + ", ".join(r["name"] for r in missing if r["abbr"] == team))
    used = {id(c) for st, c in m.values() if st == "ok"}
    gone = [p for p in summary["players"] if id(p) not in used]
    lines.append(f"\nPaid but not on that team's roster: {len(gone)} (waived players still on the books = dead money)")
    for p in sorted(gone, key=lambda p: (p["team"], -p["cost"])):
        lines.append(f"  {p['team']}: {p['name']} ${p['cost'] / 1e6:.2f}M")
    return "\n".join(lines)


def espn_context():
    con = sqlite3.connect(ROOT / "data" / "nba.sqlite")
    con.row_factory = sqlite3.Row
    latest = "SELECT abbr, name, team_id FROM team_season WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM team_season)"
    rows = list(con.execute(latest))
    teams = {r["abbr"]: r["name"] for r in rows}
    tid = {r["team_id"]: r["abbr"] for r in rows}
    roster = [{"athlete_id": r["athlete_id"], "name": r["name"], "abbr": tid.get(r["team_id"])}
              for r in con.execute("SELECT athlete_id, name, team_id FROM roster_players")]
    return teams, roster


def load():
    """The saved summary, or None. Used by the site build."""
    try:
        return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    args = sys.argv[1:]
    players_path, teams_path = (Path(args[0]), Path(args[1])) if len(args) == 2 else newest_exports()
    if not players_path or not teams_path:
        sys.exit("Couldn't find both exports (players and teams) in Downloads.")
    if kind(players_path) != "players" or kind(teams_path) != "teams":
        sys.exit("The files don't look like the players and teams contract tables.")
    teams, roster = espn_context()
    summary = build(players_path, teams_path, teams)
    summary["exported"] = datetime.fromtimestamp(Path(players_path).stat().st_mtime).date().isoformat()
    DIR.mkdir(exist_ok=True)
    if Path(players_path).resolve() != PLAYERS_FILE.resolve():
        shutil.copyfile(players_path, PLAYERS_FILE)
        shutil.copyfile(teams_path, TEAMS_FILE)
    SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=0), encoding="utf-8")
    print(report(summary, roster, teams))
