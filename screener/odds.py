"""The Odds API: current spreads and totals from US sportsbooks.

One call = (number of markets) x (number of regions) credits. Spreads + totals in
the "us" region = 2 credits per call.
"""
from __future__ import annotations

import datetime as dt
import statistics

import requests

URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"


def fetch(api_key: str, sport: str, regions: str, bookmakers: list[str], log=print) -> list[dict]:
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": "spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)
        params.pop("regions")
    r = requests.get(URL.format(sport=sport), params=params, timeout=60)
    if r.status_code == 401:
        raise RuntimeError("Odds API rejected the key (401). Check the ODDS_API_KEY secret.")
    r.raise_for_status()
    log(f"Odds API: {len(r.json())} events, credits used {r.headers.get('x-requests-used')}, "
        f"remaining {r.headers.get('x-requests-remaining')}")
    return r.json()


def summarize(event: dict) -> dict:
    """Consensus (median) and best-available lines from the home team's perspective."""
    home, away = event["home_team"], event["away_team"]
    home_spreads, totals = [], []
    best_home = best_away = best_over = best_under = None  # (point, price, book)
    for bk in event.get("bookmakers", []):
        book = bk.get("title", bk.get("key"))
        for mk in bk.get("markets", []):
            if mk["key"] == "spreads":
                for o in mk["outcomes"]:
                    pt, pr = o.get("point"), o.get("price")
                    if pt is None:
                        continue
                    if o["name"] == home:
                        home_spreads.append(pt)
                        if best_home is None or (pt, pr) > best_home[:2]:
                            best_home = (pt, pr, book)
                    elif o["name"] == away:
                        if best_away is None or (pt, pr) > best_away[:2]:
                            best_away = (pt, pr, book)
            elif mk["key"] == "totals":
                for o in mk["outcomes"]:
                    pt, pr = o.get("point"), o.get("price")
                    if pt is None:
                        continue
                    if o["name"] == "Over":
                        totals.append(pt)
                        if best_over is None or (-pt, pr) > (-best_over[0], best_over[1]):
                            best_over = (pt, pr, book)
                    elif o["name"] == "Under":
                        if best_under is None or (pt, pr) > best_under[:2]:
                            best_under = (pt, pr, book)
    start = dt.datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
    return {
        "id": event["id"],
        "home": home,
        "away": away,
        "start": start,
        "books": len(event.get("bookmakers", [])),
        "spread": statistics.median(home_spreads) if home_spreads else None,  # home line, e.g. -5.5
        "total": statistics.median(totals) if totals else None,
        "best_home": best_home,
        "best_away": best_away,
        "best_over": best_over,
        "best_under": best_under,
    }
