"""Game Analysis Standard (GAS): the data behind the morning email's per-game write-ups.

Which games: plays both models flag (the screener thresholds) that the podcast also locked on the same side
(3/3 triple lock, 2/3 double lock, or 2/2 agree).
The screener (GitHub, has the KenPom key) writes docs/gas_data.json with venue/TV, model lines, tempo,
efficiencies and Four Factors. The morning Claude run then adds Torvik player usage leaders
(`python3 -m screener.gas players`, Torvik blocks GitHub) and searches the web for injuries and the last
meeting before writing each GAS.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from .dashboard import bold  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "gas_data.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MAX_GAMES = 15


def _num(v, nd=1):
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def kenpom_url(name: str) -> str:
    """KenPom team page, e.g. 'Mississippi St.' -> https://kenpom.com/team.php?team=Mississippi+St."""
    return f"https://kenpom.com/team.php?team={quote_plus(name)}"


def _team(kp_detail: dict, name: str) -> dict:
    r = kp_detail.get("ratings", {}).get(name, {})
    ff = kp_detail.get("four-factors", {}).get(name, {})
    ms = kp_detail.get("misc-stats", {}).get(name, {})
    ht = kp_detail.get("height", {}).get(name, {})
    tm = kp_detail.get("teams", {}).get(name, {})
    pick = lambda d, keys: {k: d.get(k) for k in keys if d.get(k) not in (None, "")}
    return {
        "name": name,
        "kenpom_url": kenpom_url(name),
        "record": f"{r.get('Wins')}-{r.get('Losses')}" if r else None,
        "coach": r.get("Coach") or tm.get("Coach"),
        "conf": r.get("ConfShort") or tm.get("ConfShort"),
        "arena": ", ".join(x for x in (tm.get("Arena"), tm.get("ArenaCity"), tm.get("ArenaState")) if x) or None,
        "ratings": pick(r, ["AdjEM", "RankAdjEM", "AdjOE", "RankAdjOE", "AdjDE", "RankAdjDE", "AdjTempo",
                            "RankAdjTempo", "SOS", "RankSOS", "Luck"]),
        "four_factors": pick(ff, ["eFG_Pct", "RankeFG_Pct", "TO_Pct", "RankTO_Pct", "OR_Pct", "RankOR_Pct",
                                  "FT_Rate", "RankFT_Rate", "DeFG_Pct", "RankDeFG_Pct", "DTO_Pct", "RankDTO_Pct",
                                  "DOR_Pct", "RankDOR_Pct", "DFT_Rate", "RankDFT_Rate"]),
        "shooting": pick(ms, ["FG3Pct", "RankFG3Pct", "FG2Pct", "FTPct", "F3GRate", "RankF3GRate", "OppFG3Pct",
                              "RankOppFG3Pct", "BlockPct", "StlRate", "ARate"]),
        "size": pick(ht, ["AvgHgt", "AvgHgtRank", "HgtEff", "Exp", "ExpRank", "Bench", "Continuity"]),
    }


def qualifies(r: dict) -> bool:
    """GAS games: both models flag the play AND the podcast locked the same side (triple or double lock)."""
    pod = r.get("podcast")
    return bool(r.get("spread_flag") and pod and pod["side"] == r.get("spread_side")
                and pod["category"] in ("3/3 lock", "2/3 lock", "2/2 agree"))


def build(rows: list[dict], kp: dict, espn_events: list[dict], cfg: dict, now: dt.datetime) -> dict:
    from . import espn

    tz = ZoneInfo(cfg["display"]["timezone"])
    detail = kp.get("detail") or {}
    games = []
    for r in rows:
        pod = r.get("podcast")
        if not qualifies(r):
            continue
        flagged = True
        side = r["spread_side"]
        team = r["torvik_home"] if side == "home" else r["torvik_away"]
        k = 1 if side == "home" else -1
        line = lambda m: None if m is None else _num(k * -m)
        tags = []
        if flagged:
            tags.append("KenPom and Torvik mispriced")
        if pod:
            agrees = pod["side"] == side
            word = {"3/3 lock": "a triple lock", "2/3 lock": "a double lock", "2/2 agree": "a 2-host agree"}[pod["category"]]
            tags.append(f"{word} per TCE" + ("" if agrees or not flagged else f" on the OTHER side ({pod['team']})"))
        label = (" and ".join(tags)) if tags else ""
        fm = None
        for g in (kp.get("fanmatch") or {}).get(dt.date.fromisoformat(r["local_date"]), []):
            if {g["team_a"], g["team_b"]} == {r["torvik_home"], r["torvik_away"]}:
                fm = g
        ev = espn.match(espn_events, r["away"], r["home"]) if espn_events else None
        e = r.get("spread_edge") or {}
        games.append({
            "id": r["id"], "priority": ({"3/3 lock": 0, "2/3 lock": 1}.get(pod["category"], 2), 0 if r.get("spread_star") else 1),
            "date": r["local_date"], "tip_et": r["start"].astimezone(tz).strftime("%a %b %-d, %-I:%M %p ET"),
            "away": r["torvik_away"], "home": r["torvik_home"], "neutral": r["neutral"],
            "venue": ({"name": ev["venue"], "city": ev["city"], "neutral": ev["neutral"], "tv": ev["tv"], "note": ev["note"]}
                      if ev else None),
            "home_arena_kenpom": _team(detail, r["torvik_home"]).get("arena"),
            "pick": {"team": team, "team_bold": bold(team), "side": side, "market_line": line(-r["spread"]) if r["spread"] is not None else None,
                     "best": r.get("best_" + side)},
            "models": {"torvik_line": line(r["torvik_margin"]), "torvik_edge": _num(abs(e["torvik"])) if e.get("torvik") is not None else None,
                       "kenpom_line": line(r["kp_margin"]), "kenpom_edge": _num(abs(e["kp"])) if e.get("kp") is not None else None,
                       "kenpom_source": r.get("kp_source"), "torvik_total": _num(r.get("torvik_total")),
                       "kenpom_total": _num(r.get("kp_total")), "market_total": r.get("total"),
                       "torvik_tempo": _num(r.get("torvik_tempo")), "kenpom_tempo": _num(fm.get("tempo")) if fm else None,
                       "kenpom_home_win_prob": fm.get("home_wp") if fm else None},
            "flagged": flagged, "star": bool(r.get("spread_star")),
            "podcast": pod, "label": label,
            "teams": {"away": _team(detail, r["torvik_away"]), "home": _team(detail, r["torvik_home"])},
            "players": None,  # filled by `python -m screener.gas players`
        })
    games.sort(key=lambda g: (g["priority"], g["tip_et"]))
    return {"generated_utc": now.isoformat(timespec="minutes"), "max_games": MAX_GAMES,
            "omitted": max(0, len(games) - MAX_GAMES), "games": games[:MAX_GAMES]}


def write(data: dict) -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, default=str))


# Torvik player season stats (getadvstats.php CSV, no header). Column positions:
PCOLS = {"name": 0, "team": 1, "gp": 3, "min_pct": 4, "ortg": 5, "usg": 6, "efg": 7, "ast_pct": 11, "to_pct": 12,
         "tp_pct": 21, "tpa": 20, "yr": 25, "ht": 26, "mpg": 54, "rpg": 59, "apg": 60, "ppg": 63, "role": 64}


def add_players(path: Path = OUT, top: int = 4) -> int:
    """Add each team's top players by minutes (with usage, ORtg, role) from Torvik. Run where Torvik is reachable."""
    import requests

    data = json.loads(path.read_text())
    teams = {t["name"] for g in data["games"] for t in g["teams"].values()}
    year = None
    if data["games"]:
        d = dt.date.fromisoformat(data["games"][0]["date"])
        year = d.year + 1 if d.month >= 8 else d.year
    if not year:
        return 0
    r = requests.get(f"https://barttorvik.com/getadvstats.php?year={year}&csv=1", headers={"User-Agent": UA}, timeout=90)
    r.raise_for_status()
    by_team: dict[str, list] = {}
    for row in csv.reader(io.StringIO(r.text)):
        if len(row) <= max(PCOLS.values()) or row[1] not in teams:
            continue
        p = {k: row[i] for k, i in PCOLS.items()}
        for k in ("gp", "min_pct", "ortg", "usg", "efg", "ast_pct", "to_pct", "tp_pct", "tpa", "mpg", "rpg", "apg", "ppg"):
            p[k] = _num(p[k], 3 if k == "tp_pct" else 1)
        by_team.setdefault(row[1], []).append(p)
    for g in data["games"]:
        g["players"] = {}
        for side, t in g["teams"].items():
            ps = sorted(by_team.get(t["name"], []), key=lambda p: -(p["min_pct"] or 0))[:top]
            g["players"][side] = ps
    path.write_text(json.dumps(data, indent=1, default=str))
    return sum(1 for g in data["games"] if any(g["players"].values()))


if __name__ == "__main__":
    if sys.argv[1:] == ["players"]:
        n = add_players()
        print(f"[gas] added Torvik player data for {n} game(s)")
    else:
        print("usage: python -m screener.gas players")
