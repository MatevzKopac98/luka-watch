"""Rule-based game insights: why the winner won, and the key player.

Each rule compares the two box scores and gives its edge a weight in roughly "points of impact",
so the biggest real differences surface first.
"""


def game_score(p):
    """Hollinger Game Score: one number for a player's overall box-score impact."""
    g = lambda k: p.get(k) or 0
    return (g("pts") + 0.4 * g("fgm") - 0.7 * g("fga") - 0.4 * (g("fta") - g("ftm")) + 0.7 * g("oreb")
            + 0.3 * g("dreb") + g("stl") + 0.7 * g("ast") + 0.7 * g("blk") - 0.4 * g("pf") - g("tov"))


def stat_line(p):
    parts = [f"{p['pts']:.0f} pts"]
    for k, lbl in (("reb", "reb"), ("ast", "ast"), ("stl", "stl"), ("blk", "blk")):
        if (p.get(k) or 0) >= (3 if k in ("stl", "blk") else 5):
            parts.append(f"{p[k]:.0f} {lbl}")
    parts.append(f"{p['fgm']:.0f}/{p['fga']:.0f} FG")
    if p.get("plus_minus") is not None:
        parts.append(f"{p['plus_minus']:+.0f}")
    return " · ".join(parts)


def key_player(players, team_id):
    mine = [p for p in players if p["team_id"] == team_id and p.get("min")]
    return max(mine, key=game_score) if mine else None


def _pct(m, a):
    return 100 * m / a if a else 0


def reasons(info, w, l, w_name, l_name):
    """Top reasons the winner (box w) beat the loser (box l), most important first."""
    cand = []
    margin = abs(info["home_score"] - info["away_score"])
    g = lambda box, k: box.get(k) or 0

    if g(l, "largest_lead") >= 10:
        cand.append((100, f"Came back from {g(l, 'largest_lead'):.0f} points down"))
    d3 = g(w, "fg3m") - g(l, "fg3m")
    if d3 >= 4:
        cand.append((3 * d3, f"Hit {g(w, 'fg3m'):.0f} threes to {g(l, 'fg3m'):.0f} "
                             f"(+{3 * d3:.0f} points from deep)"))
    dreb = g(w, "reb") - g(l, "reb")
    if dreb >= 6:
        extra = f", including {g(w, 'oreb'):.0f} offensive" if g(w, "oreb") >= 12 else ""
        cand.append((1.2 * dreb, f"Won the glass {g(w, 'reb'):.0f}–{g(l, 'reb'):.0f}{extra}"))
    dtov = g(l, "tov") - g(w, "tov")
    off_tov = g(l, "pts_off_tov")   # ESPN stores points conceded after own turnovers -> loser's value = winner's scoring
    if dtov >= 4:
        cand.append((1.3 * dtov + 0.3 * off_tov,
                     f"Forced {g(l, 'tov'):.0f} turnovers, committed {g(w, 'tov'):.0f}"
                     + (f" ({off_tov:.0f} points off turnovers)" if off_tov else "")))
    dft = g(w, "ftm") - g(l, "ftm")
    if dft >= 6:
        cand.append((dft, f"Made {g(w, 'ftm'):.0f} free throws to {g(l, 'ftm'):.0f}"))
    dpaint = g(w, "paint") - g(l, "paint")
    if dpaint >= 12:
        cand.append((dpaint / 2, f"Owned the paint {g(w, 'paint'):.0f}–{g(l, 'paint'):.0f}"))
    dfb = g(w, "fastbreak") - g(l, "fastbreak")
    if dfb >= 10:
        cand.append((dfb / 2, f"Ran: {g(w, 'fastbreak'):.0f}–{g(l, 'fastbreak'):.0f} in fast-break points"))
    dfg = _pct(g(w, "fgm"), g(w, "fga")) - _pct(g(l, "fgm"), g(l, "fga"))
    if dfg >= 5:
        cand.append((dfg * 0.9, f"Shot {_pct(g(w, 'fgm'), g(w, 'fga')):.0f}% from the field, "
                                f"held {l_name} to {_pct(g(l, 'fgm'), g(l, 'fga')):.0f}%"))
    dast = g(w, "ast") - g(l, "ast")
    if dast >= 8:
        cand.append((dast * 0.6, f"Moved the ball: {g(w, 'ast'):.0f} assists to {g(l, 'ast'):.0f}"))
    cand.sort(key=lambda c: -c[0])
    out = [c[1] for c in cand[:2]]
    if not out and margin <= 5:
        out.append("Decided by a few late possessions")
    return out


def tags(info, l_box):
    """Short labels for the game card."""
    margin = abs(info["home_score"] - info["away_score"])
    periods = len((info.get("home_lines") or "").split(","))
    t = []
    if periods > 4:
        t.append("OT" if periods == 5 else f"{periods - 4}OT")
    if margin <= 5:
        t.append("Close")
    elif margin >= 20:
        t.append("Blowout")
    if (l_box.get("largest_lead") or 0) >= 10:
        t.append("Comeback")
    return t
