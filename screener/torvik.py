"""Torvik (barttorvik.com) data: full-season schedule with T-Rank game predictions and results.

The {year}_super_sked.json file holds one row per game. Field positions used here:
  1 date (m/d/yy)   3 matchup text   7 neutral flag (1 = neutral)
  8 team1 (away / first listed)      13 team1 projected points
  14 team2 (home / second listed)    19 team2 projected points
  20 projected tempo                 23 game played flag (1 = final)
  27 team1 final points              28 team2 final points
"""
from __future__ import annotations

import datetime as dt
import time

import requests

BASE = "https://barttorvik.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def season_year(day: dt.date) -> int:
    """Torvik labels a season by the year it ends (2026-27 season -> 2027)."""
    return day.year + 1 if day.month >= 8 else day.year


def _get_json(url: str, tries: int = 3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # network blip or bot-check page
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"Torvik download failed for {url}: {last}")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_schedule(year: int) -> list[dict]:
    rows = _get_json(f"{BASE}/{year}_super_sked.json")
    games = []
    for r in rows:
        if len(r) < 29:
            continue
        try:
            day = dt.datetime.strptime(r[1], "%m/%d/%y").date()
        except (TypeError, ValueError):
            continue
        t1, t2 = r[8], r[14]
        p1, p2 = _num(r[13]), _num(r[19])
        played = str(r[23]) == "1"
        games.append(
            {
                "date": day,
                "away": t1,
                "home": t2,
                "neutral": str(r[7]) == "1",
                "matchup": r[3],
                "torvik_text": r[4],
                # Home-team perspective: positive margin = home team wins by that many.
                "torvik_margin": (p2 - p1) if p1 is not None and p2 is not None else None,
                "torvik_total": (p1 + p2) if p1 is not None and p2 is not None else None,
                "torvik_tempo": _num(r[20]),
                "final": played,
                "away_pts": _num(r[27]) if played else None,
                "home_pts": _num(r[28]) if played else None,
            }
        )
    return games
