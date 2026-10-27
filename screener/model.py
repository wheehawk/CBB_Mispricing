"""Projections and edges. Everything is expressed from the SPORTSBOOK's home team perspective.

margin  = projected (home points - away points)
spread  = market line on the home team (home -5.5 means home favored by 5.5)
spread edge = margin + spread   (> 0: value on home side, < 0: value on away side)
total edge  = projected total - market total   (> 0: over, < 0: under)
"""
from __future__ import annotations

import statistics


def kenpom_from_ratings(ratings: dict, home: str, away: str, neutral: bool, hca: float):
    h, a = ratings.get(home), ratings.get(away)
    if not h or not a:
        return None
    avg_eff = statistics.mean(r["adjo"] for r in ratings.values())
    avg_t = statistics.mean(r["adjt"] for r in ratings.values())
    poss = h["adjt"] * a["adjt"] / avg_t
    ph = h["adjo"] * a["adjd"] / avg_eff * poss / 100
    pa = a["adjo"] * h["adjd"] / avg_eff * poss / 100
    return {"margin": ph - pa + (0 if neutral else hca), "total": ph + pa, "source": "ratings"}


def kenpom_from_fanmatch(fm_games: list[dict], home: str, away: str):
    for g in fm_games:
        if {g["team_a"], g["team_b"]} == {home, away}:
            diff = g["winner_pts"] - g["loser_pts"]
            margin = diff if g["winner"] == home else -diff
            return {"margin": margin, "total": g["winner_pts"] + g["loser_pts"], "source": "FanMatch"}
    return None


def blend(values: list[tuple[float | None, float]]):
    vals = [(v, w) for v, w in values if v is not None and w > 0]
    if not vals:
        return None
    return sum(v * w for v, w in vals) / sum(w for _, w in vals)


def evaluate(row: dict, cfg: dict) -> dict:
    """Add edges and flags to a game row that already has market + model numbers."""
    th, md = cfg["thresholds"], cfg["model"]
    tw, kw = md["torvik_weight"], md["kenpom_weight"]

    row["blend_margin"] = blend([(row["torvik_margin"], tw), (row["kp_margin"], kw)])
    row["blend_total"] = blend([(row["torvik_total"], tw), (row["kp_total"], kw)])

    def edges(kind, market, keys, threshold):
        if market is None:
            return None
        e = {}
        for k in keys:
            v = row[f"{k}_{kind}"]
            if v is None:
                e[k] = None
            else:
                e[k] = (v + market) if kind == "margin" else (v - market)
        blend_e = e["blend"]
        flagged = blend_e is not None and abs(blend_e) >= threshold
        singles = [e[k] for k in ("torvik", "kp") if e[k] is not None]
        if flagged and th["require_model_agreement"] and len(singles) == 2:
            same_side = (singles[0] > 0) == (singles[1] > 0)
            flagged = same_side and min(abs(x) for x in singles) >= th["min_single_model_edge"]
        return e, flagged

    s = edges("margin", row["spread"], ("torvik", "kp", "blend"), th["spread_edge"])
    t = edges("total", row["total"], ("torvik", "kp", "blend"), th["total_edge"])
    row["spread_edge"] = s[0] if s else None
    row["spread_flag"] = bool(s and s[1])
    row["total_edge"] = t[0] if t else None
    row["total_flag"] = bool(t and t[1])
    if row["spread_flag"]:
        row["spread_side"] = "home" if row["spread_edge"]["blend"] > 0 else "away"
    if row["total_flag"]:
        row["total_side"] = "over" if row["total_edge"]["blend"] > 0 else "under"
    return row
