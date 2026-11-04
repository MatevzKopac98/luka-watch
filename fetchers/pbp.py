"""Turn ESPN play-by-play into stints: stretches of play with the same ten players on the court.

Each stint carries both lineups, its length, and what each team did in it (points, field-goal and
free-throw attempts, offensive rebounds, turnovers), which is enough to estimate possessions and
ratings for any player or lineup.

ESPN often doesn't log substitutions made between quarters, so each quarter's starting five is
inferred: a player who is subbed out, or shows up in a play, before being subbed in that quarter
must have started it. Gaps are filled from the previous quarter's closing lineup.
"""
from collections import Counter

STAT_KEYS = ("pts", "fga", "fta", "oreb", "tov")


def _secs(clock):
    """'7:04' -> 424.0, '45.3' -> 45.3 (time remaining in the period)."""
    parts = str(clock or "0").split(":")                       # "45.3", "7:04" or occasionally "0:07:04"
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p or 0)
    return secs


def _period_len(p):
    return 720 if p <= 4 else 300


def _period_start(p):
    return sum(_period_len(q) for q in range(1, p))


def _elapsed(period, clock):
    return _period_start(period) + _period_len(period) - _secs(clock)


def _ids(play):
    return [(x.get("athlete") or {}).get("id") for x in play.get("participants") or []
            if (x.get("athlete") or {}).get("id")]


def _kind(play):
    return (play.get("type") or {}).get("text", "")


def _infer_starters(pplays, team_of, teams, prev_end):
    """Starting five of each team for one period (ESPN skips most between-quarter subs)."""
    lineup = {t: [] for t in teams}
    subbed_in, votes = set(), Counter()
    for p in pplays:
        ids = _ids(p)
        if _kind(p) == "Substitution" and len(ids) == 2:
            incoming, outgoing = ids
            t = team_of.get(outgoing)
            if t in lineup and outgoing not in subbed_in and outgoing not in lineup[t]:
                lineup[t].append(outgoing)                      # left before ever coming in -> started
            subbed_in.add(incoming)
        elif "Technical" not in _kind(p) and "Ejection" not in _kind(p):
            for pid in ids:
                if pid not in subbed_in and team_of.get(pid) in lineup:
                    votes[pid] += 1                             # played before being subbed in -> started
    for pid, _ in votes.most_common():
        t = team_of[pid]
        if pid not in lineup[t] and len(lineup[t]) < 5:
            lineup[t].append(pid)
    for t in teams:                                             # quiet players: carry over from last period
        for pid in prev_end.get(t, []):
            if len(lineup[t]) >= 5:
                break
            if pid not in lineup[t] and pid not in subbed_in:
                lineup[t].append(pid)
    return {t: set(v[:5]) for t, v in lineup.items()}


def build_stints(summary):
    """Returns (stints, quality) for one game summary. quality = share of game time with 5-v-5 known."""
    comp = summary["header"]["competitions"][0]
    side = {c["homeAway"]: c["team"]["id"] for c in comp["competitors"]}
    home, away = side["home"], side["away"]
    team_of, starters = {}, {home: set(), away: set()}
    for t in summary.get("boxscore", {}).get("players", []):
        tid = t["team"]["id"]
        for a in (t.get("statistics") or [{}])[0].get("athletes", []):
            if not (a.get("athlete") or {}).get("id"):          # some NBA Cup box scores carry an empty entry
                continue
            team_of[a["athlete"]["id"]] = tid
            if a.get("starter"):
                starters[tid].add(a["athlete"]["id"])
    plays = summary.get("plays") or []
    if not plays or not team_of:
        return [], 0.0
    event_id = summary["header"]["id"]
    pre = {home: "home", away: "away"}
    stints = []
    score = (0, 0)
    prev_end = {t: list(starters[t]) for t in (home, away)}

    def new(period, start, on):
        return {"event_id": event_id, "period": period, "start_sec": round(start, 1), "seconds": 0.0,
                "home_ids": "|".join(sorted(on[home])), "away_ids": "|".join(sorted(on[away])),
                **{f"{s}_{k}": 0 for s in ("home", "away") for k in STAT_KEYS}, "_score0": score}

    def close(st, end):
        st["seconds"] = round(end - st["start_sec"], 1)
        st["home_pts"] = max(score[0] - st["_score0"][0], 0)
        st["away_pts"] = max(score[1] - st["_score0"][1], 0)
        del st["_score0"]
        if st["seconds"] > 0:
            stints.append(st)

    for pnum in sorted({p["period"]["number"] for p in plays}):
        pplays = [p for p in plays if p["period"]["number"] == pnum]
        if pnum == 1 and len(starters[home]) == 5 and len(starters[away]) == 5:
            on = {t: set(starters[t]) for t in (home, away)}
        else:
            on = _infer_starters(pplays, team_of, (home, away), prev_end)
        cur = new(pnum, _period_start(pnum), on)
        for p in pplays:
            ids = _ids(p)
            if _kind(p) == "Substitution" and len(ids) == 2:
                now = _elapsed(pnum, (p.get("clock") or {}).get("displayValue"))
                if now > cur["start_sec"]:                      # several subs at one stoppage = one boundary
                    close(cur, now)
                    cur = new(pnum, now, on)
                incoming, outgoing = ids
                t = team_of.get(incoming) or team_of.get(outgoing)
                if t in on:
                    on[t].discard(outgoing)
                    on[t].add(incoming)
                    cur[f"{pre[t]}_ids"] = "|".join(sorted(on[t]))
                continue
            t = (p.get("team") or {}).get("id")
            if t in pre:
                k = _kind(p)
                if k.startswith("Free Throw"):
                    cur[f"{pre[t]}_fta"] += 1
                elif p.get("shootingPlay"):
                    cur[f"{pre[t]}_fga"] += 1
                elif k == "Offensive Rebound" and ids:              # no participant = team rebound, not counted
                    cur[f"{pre[t]}_oreb"] += 1
                if "Turnover" in k:
                    cur[f"{pre[t]}_tov"] += 1
            if p.get("homeScore") is not None:
                score = (int(p["homeScore"]), int(p["awayScore"]))
        close(cur, _period_start(pnum) + _period_len(pnum))
        prev_end = {t: list(on[t]) for t in (home, away)}

    total = sum(s["seconds"] for s in stints)
    known = sum(s["seconds"] for s in stints
                if len(s["home_ids"].split("|")) == 5 and len(s["away_ids"].split("|")) == 5)
    return stints, (known / total if total else 0.0)
