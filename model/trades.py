"""Trade ideas for the Lakers: realistic packages that make the team better without thinning the rotation.

How an idea is found and checked:
1. Weak spots: the Lakers' league ranks (offense, defense, rebounding, rim protection, steals, turnovers, threes).
2. Every package of 1-2 Lakers for 1-2 players from one other team (Luka is untouchable; never 3-for-1).
3. Salary rules of the 2023 CBA with 2026-27 numbers, for BOTH teams (matching tiers, aprons, cap room).
4. Depth and workload: minutes are re-spread over the healthy roster after the trade. A trade is dropped if any
   player already on the team would need more than MAX_EXTRA_MIN extra minutes a game, anyone would go over
   MAX_MIN, the rotation shrinks below MIN_ROTATION players, or it leaves the team short of bigs or guards.
5. Better: the Lakers' projected net rating (lineup-model impact x minutes) must rise by MIN_GAIN.
6. Would the other team say yes? Their direction (rebuilding / middle / contending) and what they get: surplus
   value (impact for the money over the contract), youth, salary savings or expiring money for sellers, and not
   getting worse for contenders. Ideas the other team would clearly refuse are dropped.

Limits, stated on the page: draft picks, no-trade clauses and front-office preferences aren't known, and players
who signed as free agents in the summer can't be traded until 15 December.
"""
from dataclasses import dataclass, field
from itertools import combinations

# 2026-27 league figures (NBA, June 2026) and trade-matching tiers (2023 CBA, indexed to the 2026-27 cap)
CAP, TAX, APRON1, APRON2 = 164_961_000, 200_428_000, 209_015_000, 221_686_000
TIER1, TIER2 = 9_100_000, 35_200_000          # 200% + 250K up to TIER1, +9.1M up to TIER2, then 125% + 250K
ROOM_BUFFER = 100_000

LAKERS_ID = "13"
UNTOUCHABLE = {"Luka Doncic", "Luka Dončić"}
MAX_PER_SIDE = 2              # at most 2 players each way, and never more than one extra on either side
MIN_GAIN = 0.6                # net rating points per 100 possessions the Lakers must gain
MAX_MIN = 36.5                # nobody projected above this many minutes a game (stars already there: +0.5)
MAX_EXTRA_MIN = 2.5           # a player already on the team may take on at most this many extra minutes
MIN_ROTATION = 9              # healthy players projected at 12+ minutes
MIN_POSS = 1500               # possessions of lineup data before a player's impact is trusted as a target
MIN_POSS_ANY = 500            # every incoming player needs at least this much data
SHOWN = 5


@dataclass
class P:
    pid: str
    name: str
    team: str
    pos: str
    age: int
    salary: float
    years: int
    off: float
    dfn: float                # points allowed added per 100 (negative = better defender)
    poss: float
    mpg: float
    out: bool
    stats: dict = field(default_factory=dict)   # per 36: reb, blk, stl, tov, fg3m, fg3a, ast

    @property
    def net(self):
        return self.off - self.dfn


def allowed_incoming(outgoing, payroll_before, incoming, n_out):
    """(ok, limit, rule) for one team under the salary-matching rules."""
    after = payroll_before - outgoing + incoming
    if after <= CAP + ROOM_BUFFER:
        return True, CAP + ROOM_BUFFER - (payroll_before - outgoing), "fits under the cap"
    if after > APRON2:
        if n_out > 1:
            return False, outgoing, "over the second apron: salaries can't be combined"
        return incoming <= outgoing, outgoing, "over the second apron: can take back up to 100%"
    if after > APRON1:
        return incoming <= outgoing, outgoing, "over the first apron: can take back up to 100%"
    if outgoing <= TIER1:
        limit = 2 * outgoing + 250_000
    elif outgoing <= TIER2:
        limit = outgoing + TIER1
    else:
        limit = 1.25 * outgoing + 250_000
    return incoming <= limit, limit, "over the cap, under the first apron"


def allocate(players):
    """Expected minutes after spreading 240 over the healthy roster. Returns ({pid: minutes}, feasible)."""
    healthy = [p for p in players if not p.out]
    base = {p.pid: max(p.mpg, 2.0) for p in healthy}
    cap = {p.pid: min(max(p.mpg + 3, p.mpg * 1.12, 10.0), MAX_MIN + (0.5 if p.mpg >= 35 else 0)) for p in healthy}
    total = sum(base.values())
    if total >= 240:
        # More minutes per game than a game has (players missed games): trim, mostly from the bench. Minutes go
        # as mpg^k with k chosen so the busiest player keeps his usual minutes and the deep bench gives up the most.
        top = max(base.values())
        lo, hi = 1.0, 6.0
        for _ in range(40):
            k = (lo + hi) / 2
            scale = 240 / sum(v ** k for v in base.values())
            lo, hi = (k, hi) if scale * top ** k < top else (lo, k)
        scale = 240 / sum(v ** k for v in base.values())
        return {pid: scale * v ** k for pid, v in base.items()}, True
    mins, need = dict(base), 240 - total
    for _ in range(50):                                   # raise everyone in proportion, up to their cap
        room = {k: cap[k] - mins[k] for k in mins if cap[k] - mins[k] > 1e-6}
        if need <= 1e-6 or not room:
            break
        weight = sum(mins[k] for k in room)
        for k in room:
            add = min(room[k], need * mins[k] / weight)
            mins[k] += add
        need = 240 - sum(mins.values())
    return mins, need <= 0.5


def rating(players, league):
    """Projected (offense, defense) per 100 possessions from impact x minutes share."""
    mins, ok = allocate(players)
    by = {p.pid: p for p in players}
    o = league + sum(m / 48 * by[k].off for k, m in mins.items())
    d = league + sum(m / 48 * by[k].dfn for k, m in mins.items())
    return o, d, mins, ok


WEAK_STATS = [("drtg", "defense", True, None), ("ortg", "offense", False, None),
              ("reb", "rebounding", False, "reb"), ("blk", "rim protection", False, "blk"),
              ("stl", "forcing turnovers", False, "stl"), ("tov", "ball security", True, "tov"),
              ("fg3_pct", "three-point shooting", False, "fg3m")]


def weaknesses(teams, team_id, n=3):
    """The team's worst league ranks: [(label, rank, stat key for players or None)]."""
    out = []
    for key, label, low, pstat in WEAK_STATS:
        vals = sorted((t[key] for t in teams.values() if t.get(key) is not None), reverse=not low)
        v = teams[team_id].get(key)
        if v is None:
            continue
        out.append((label, vals.index(v) + 1, pstat))
    return sorted(out, key=lambda w: -w[1])[:n]


def production(p):
    """On-court value: impact above replacement, convex (stars are worth far more than two good players)."""
    return max(p.net + 2.5, 0) ** 1.6 * min(p.mpg, 36) / 36


def surplus(p, worth_per_point):
    """Value over the contract: what his impact is worth minus what he's paid, younger players valued higher."""
    per_year = production(p) * worth_per_point - p.salary
    years = max(p.years, 1)
    age_factor = 1.15 if p.age and p.age <= 24 else 0.85 if p.age and p.age >= 31 else 1.0
    return per_year * min(years, 4) * age_factor


def direction(team):
    gp = (team.get("wins") or 0) + (team.get("losses") or 0)
    pct = team.get("win_pct") or 0.5
    if gp < 6:
        pct = 0.5 + (team.get("net_rtg") or 0) / 30
    return "seller" if pct < 0.42 else "contender" if pct >= 0.58 else "middle"


def ideas(players, teams, payroll, league, team_id=LAKERS_ID):
    """Ranked trade ideas for team_id. players: {pid: P} for every rostered player with a known contract."""
    by_team = {}
    for p in players.values():
        by_team.setdefault(p.team, []).append(p)
    mine = by_team.get(team_id, [])
    if not mine:
        return [], []
    rot = [p for p in players.values() if p.mpg >= 12 and p.salary > 0]
    worth_per_point = sum(p.salary for p in rot) / (sum(production(p) for p in rot) or 1)
    o0, d0, mins0, _ = rating(mine, league)
    weak = weaknesses(teams, team_id)
    out_pool = [p for p in mine if p.name not in UNTOUCHABLE and p.salary > 0]
    found = []
    for other, theirs in by_team.items():
        if other == team_id or other not in teams:
            continue
        their_o0, their_d0, their_mins0, _ = rating(theirs, league)
        dirn = direction(teams[other])
        # Teams don't move their two best players, unless they're rebuilding and the player is a veteran
        core = {p.pid for p in sorted(theirs, key=lambda p: -production(p))[:2]}
        in_pool = [p for p in theirs if p.salary > 0 and not p.out
                   and (p.pid not in core or (dirn == "seller" and (p.age or 0) >= 29))
                   and p.poss >= MIN_POSS_ANY                   # unknown quality: not a sensible target
                   and (p.age or 30) > 22]                      # young prospects: teams keep them
        for n_out in range(1, MAX_PER_SIDE + 1):
            for send in combinations(out_pool, n_out):
                s_out = sum(p.salary for p in send)
                for n_in in range(1, MAX_PER_SIDE + 1):
                    if abs(n_in - n_out) > 1:
                        continue
                    for get in combinations(in_pool, n_in):
                        if max(p.poss for p in get) < MIN_POSS:        # need at least one well-measured target
                            continue
                        s_in = sum(p.salary for p in get)
                        ok1, lim1, rule1 = allowed_incoming(s_out, payroll[team_id], s_in, n_out)
                        ok2, lim2, rule2 = allowed_incoming(s_in, payroll[other], s_out, n_in)
                        if not (ok1 and ok2):
                            continue
                        idea = evaluate(team_id, other, send, get, mine, theirs, league, (o0, d0, mins0),
                                        (their_o0, their_d0, their_mins0), dirn, worth_per_point, weak)
                        if idea:
                            idea.update(salary={"out": s_out, "in": s_in, "lim1": lim1, "rule1": rule1,
                                                "lim2": lim2, "rule2": rule2,
                                                "pay1": payroll[team_id] - s_out + s_in,
                                                "pay2": payroll[other] - s_in + s_out})
                            found.append(idea)
    found.sort(key=lambda i: -i["score"])
    shown, used_targets, per_team, sent = [], set(), {}, {}
    for i in found:                          # variety: different targets and teams, no Laker in more than 2 ideas
        target = i["get"][0].pid
        if (target in used_targets or per_team.get(i["other"], 0) >= 1
                or any(sent.get(p.pid, 0) >= 2 for p in i["send"])):
            continue
        shown.append(i)
        used_targets.add(target)
        per_team[i["other"]] = per_team.get(i["other"], 0) + 1
        for p in i["send"]:
            sent[p.pid] = sent.get(p.pid, 0) + 1
        if len(shown) == SHOWN:
            break
    return shown, weak


def _depth_problem(before, after_players, mins_before, mins_after):
    """Why a roster would be too thin after the trade, or None."""
    stay = [p for p in after_players if p.pid in mins_before]
    for p in stay:
        extra = mins_after.get(p.pid, 0) - mins_before.get(p.pid, 0)
        if extra > MAX_EXTRA_MIN:
            return f"{p.name} would need {extra:.1f} more minutes a game"
    healthy = [p for p in after_players if not p.out]
    if sum(1 for p in healthy if mins_after.get(p.pid, 0) >= 12) < MIN_ROTATION:
        return "rotation would shrink below nine players"
    for pos, need in (("C", 2), ("G", 4)):
        had = sum(1 for p in before if p.pos == pos and not p.out)
        has = sum(1 for p in healthy if p.pos == pos)
        if has < min(had, need):
            return f"too few {'bigs' if pos == 'C' else 'guards'} left"
    standard_before = sum(1 for p in before if p.salary > 0)   # standard contracts (two-way deals don't count)
    standard_after = sum(1 for p in after_players if p.salary > 0)
    if standard_after > max(15, standard_before) or standard_after < min(13, standard_before):
        return "roster size"
    return None


def evaluate(team_id, other, send, get, mine, theirs, league, base, their_base, dirn, worth_per_point, weak):
    o0, d0, mins0 = base
    send_ids, get_ids = {p.pid for p in send}, {p.pid for p in get}
    after = [p for p in mine if p.pid not in send_ids] + list(get)
    o1, d1, mins1, feasible = rating(after, league)
    if not feasible:
        return None
    gain = (o1 - d1) - (o0 - d0)
    if gain < MIN_GAIN:
        return None
    if _depth_problem(mine, after, mins0, mins1):
        return None
    their_after = [p for p in theirs if p.pid not in get_ids] + list(send)
    to1, td1, tmins1, tfeasible = rating(their_after, league)
    if not tfeasible or _depth_problem(theirs, their_after, their_base[2], tmins1):
        return None
    their_gain = (to1 - td1) - (their_base[0] - their_base[1])
    value_gain = sum(surplus(p, worth_per_point) for p in send) - sum(surplus(p, worth_per_point) for p in get)
    age_in = sum(p.age or 27 for p in send) / len(send)
    age_out = sum(p.age or 27 for p in get) / len(get)
    savings = sum(p.salary for p in get) - sum(p.salary for p in send)
    expiring = sum(p.salary for p in send if p.years <= 1)
    # Would they say yes? (positive = good for them)
    appeal = value_gain / 10e6
    reasons = []
    if dirn == "seller":
        appeal += 0.4 * max(age_out - age_in, 0) / 3 + savings / 20e6 + expiring / 40e6
        if age_out - age_in >= 2:
            reasons.append(f"they get younger (average age {age_in:.0f} vs {age_out:.0f})")
        if savings > 1e6:
            reasons.append(f"they save ${savings / 1e6:.1f}M this season")
        if expiring > 3e6:
            reasons.append(f"${expiring / 1e6:.1f}M of what they take back expires next summer, freeing cap space")
    elif dirn == "contender":
        appeal += their_gain / 1.5
        if their_gain > -0.3:
            reasons.append(f"their projected rating {'improves' if their_gain > 0.2 else 'barely changes'} "
                           f"({their_gain:+.1f})")
    else:
        appeal += their_gain / 3 + savings / 30e6
        if savings > 1e6:
            reasons.append(f"they save ${savings / 1e6:.1f}M this season")
        if their_gain > 0.2:
            reasons.append(f"their projected rating improves ({their_gain:+.1f})")
    if value_gain > 2e6:
        reasons.append("they get more value for the money over the contracts")
    if dirn == "contender" and their_gain < -1.0:
        return None
    if appeal < -0.8:
        return None                                           # they'd clearly say no
    label = "Realistic" if appeal >= 0 else "Possible"
    fits = []
    for lbl, rk, stat in weak:
        if stat:
            sign = -1 if stat == "tov" else 1
            d = sum(p.stats.get(stat, 0) * mins1.get(p.pid, 0) / 36 for p in get) - \
                sum(p.stats.get(stat, 0) * mins0.get(p.pid, 0) / 36 for p in send)
            if sign * d > 0.3:
                fits.append((lbl, rk))
        elif lbl == "defense" and (d0 - d1) > 0.3:
            fits.append((lbl, rk))
        elif lbl == "offense" and (o1 - o0) > 0.3:
            fits.append((lbl, rk))
    moved = sorted(((mins1.get(p.pid, 0) - mins0.get(p.pid, 0), p) for p in after if p.pid in mins0),
                   key=lambda t: -t[0])
    score = gain + 0.25 * len(fits) + 0.5 * min(appeal, 1.0) + (0.2 if len(send) == len(get) else 0)
    return {"other": other, "send": list(send), "get": list(get), "gain": gain, "off": o1 - o0, "def": d0 - d1,
            "their_gain": their_gain, "direction": dirn, "appeal": appeal, "label": label, "reasons": reasons,
            "fits": fits, "score": score, "mins_before": mins0, "mins_after": mins1, "most_extra": moved[:2],
            "rotation": sum(1 for p in after if not p.out and mins1.get(p.pid, 0) >= 12),
            "roster_change": len(get) - len(send)}
