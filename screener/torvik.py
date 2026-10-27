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
import json
import time
from pathlib import Path

import requests

BASE = "https://barttorvik.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def season_year(day: dt.date) -> int:
    """Torvik labels a season by the year it ends (2026-27 season -> 2027)."""
    return day.year + 1 if day.month >= 8 else day.year


def _get_json_browser(url: str):
    """Torvik sometimes answers plain downloads with a "Verifying your browser" page (403),
    e.g. from GitHub's servers. A headless browser passes that check, then downloads the file
    with the same cookies."""
    import asyncio

    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(user_agent=UA)
            page = await ctx.new_page()
            await page.goto(f"{BASE}/", timeout=60000)
            for _ in range(10):  # the check auto-submits and reloads
                await page.wait_for_timeout(3000)
                if "verifying" not in (await page.title()).lower():
                    break
            # Download from inside the verified page (same cookies, same network path).
            status, body = await page.evaluate(
                "async u => { const r = await fetch(u, {credentials: 'include'}); return [r.status, await r.text()]; }",
                url,
            )
            if status != 200 or body.lstrip()[:1] != "[":
                # Fall back to loading the file as a page.
                resp = await page.goto(url, timeout=120000)
                status = resp.status if resp else status
                body = await page.evaluate("document.body.innerText")
            await browser.close()
            if body.lstrip()[:1] != "[":
                raise RuntimeError(f"HTTP {status}, got a non-data page: {body[:120]!r}")
            return json.loads(body)

    return asyncio.run(run())


def _get_json(url: str, tries: int = 2):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # network blip or bot-check page
            last = e
            time.sleep(3 * (i + 1))
    try:
        print(f"[screener] Plain Torvik download blocked ({last}); retrying with a headless browser", flush=True)
        return _get_json_browser(url)
    except Exception as e:
        raise RuntimeError(f"Torvik download failed for {url}: {last}; browser attempt: {e}") from e


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


CACHE_DAYS_BACK, CACHE_DAYS_AHEAD = 5, 9


def load_schedule(year: int, cache_path=None, log=print) -> list[dict]:
    """Live Torvik schedule; if Torvik blocks this machine (it blocks GitHub's servers),
    fall back to the stored copy in data/torvik_cache.json written by `python -m screener.torvik_feed`."""
    try:
        return parse_rows(_get_json(f"{BASE}/{year}_super_sked.json"))
    except Exception as e:
        if not cache_path or not Path(cache_path).exists():
            raise
        cache = json.loads(Path(cache_path).read_text())
        fetched = dt.datetime.fromisoformat(cache["fetched_utc"])
        age_h = (dt.datetime.now(dt.timezone.utc) - fetched).total_seconds() / 3600
        log(f"Torvik live download unavailable ({str(e)[:80]}...); using stored copy from {age_h:.0f}h ago")
        if age_h > 30:
            log(f"WARNING: stored Torvik data is {age_h:.0f} hours old; projections may be stale.")
        games = []
        for g in cache["games"]:
            g = dict(g)
            g["date"] = dt.date.fromisoformat(g["date"])
            games.append(g)
        return games


def write_cache(path, games: list[dict], today: dt.date) -> int:
    """Save a compact copy of games near today (keeps the repo small)."""
    lo, hi = today - dt.timedelta(days=CACHE_DAYS_BACK), today + dt.timedelta(days=CACHE_DAYS_AHEAD)
    keep = [{**g, "date": g["date"].isoformat()} for g in games if lo <= g["date"] <= hi]
    payload = {"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "games": keep}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, separators=(",", ":")))
    return len(keep)


def parse_rows(rows) -> list[dict]:
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
