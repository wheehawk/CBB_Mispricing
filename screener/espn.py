"""Venue and TV details from ESPN's public scoreboard feed (for the GAS write-ups). Best effort: never raises."""
from __future__ import annotations

import datetime as dt

import requests
from rapidfuzz import fuzz

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"


def fetch(dates: list[dt.date], log=print) -> list[dict]:
    events = []
    for d in dates:
        try:
            r = requests.get(URL, params={"dates": d.strftime("%Y%m%d"), "groups": 50, "limit": 500}, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log(f"ESPN scoreboard for {d} unavailable ({str(e)[:80]})")
            continue
        for ev in data.get("events", []):
            try:
                c = ev["competitions"][0]
                teams = {t["homeAway"]: t["team"] for t in c["competitors"]}
                v = c.get("venue") or {}
                addr = v.get("address") or {}
                tv = sorted({n for b in c.get("broadcasts", []) for n in b.get("names", [])})
                events.append({
                    "home": teams["home"].get("displayName", ""), "away": teams["away"].get("displayName", ""),
                    "start": ev.get("date"), "neutral": bool(c.get("neutralSite")),
                    "venue": v.get("fullName", ""), "city": ", ".join(x for x in (addr.get("city"), addr.get("state")) if x),
                    "tv": tv, "note": "; ".join(n.get("headline", "") for n in c.get("notes", []) if n.get("headline")),
                })
            except (KeyError, IndexError, TypeError):
                continue
    return events


def match(events: list[dict], away: str, home: str) -> dict | None:
    """Best ESPN event for a sportsbook game (names like "Iona Gaels"), either orientation."""
    best, score = None, 0
    for e in events:
        s1 = fuzz.token_sort_ratio(away, e["away"]) + fuzz.token_sort_ratio(home, e["home"])
        s2 = fuzz.token_sort_ratio(away, e["home"]) + fuzz.token_sort_ratio(home, e["away"])
        s = max(s1, s2)
        if s > score:
            best, score = e, s
    return best if score >= 170 else None
