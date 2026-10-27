"""KenPom data via a headless browser (kenpom.com sits behind a bot check).

Two sources:
  * Public ratings table (no login): AdjO, AdjD, AdjT for every team. Used to compute
    projections when FanMatch is unavailable.
  * FanMatch (subscriber login): KenPom's own predicted score for each game that day.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re

from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# KenPom name -> Torvik name, for the handful that differ.
KP_TO_TORVIK = {
    "CSUN": "Cal St. Northridge",
    "Kansas City": "UMKC",
    "LSU New Orleans": "New Orleans",
    "McNeese": "McNeese St.",
    "Nicholls": "Nicholls St.",
    "SIUE": "SIU Edwardsville",
    "Southeast Missouri": "Southeast Missouri St.",
}


def _clean_team(name: str) -> str:
    name = re.sub(r"\s+\d+$", "", name.strip())  # tournament seed suffix
    return KP_TO_TORVIK.get(name, name)


def _f(s):
    try:
        return float(str(s).replace("+", ""))
    except ValueError:
        return None


def parse_ratings(html: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="ratings-table")
    out = {}
    if table is None:
        return out
    for tr in table.find_all("tr"):
        td = [c.get_text(strip=True) for c in tr.find_all("td")]
        if len(td) < 10 or not td[0].isdigit():
            continue
        o, d, t = _f(td[5]), _f(td[7]), _f(td[9])
        if None in (o, d, t):
            continue
        out[_clean_team(td[1])] = {"adjo": o, "adjd": d, "adjt": t}
    return out


_PRED = re.compile(r"^(?P<winner>.+?) (?P<w>\d+)-(?P<l>\d+) \((?P<prob>\d+)%\)")


def parse_fanmatch(html: str) -> list[dict]:
    """Return [{team_a, team_b, neutral, winner, winner_pts, loser_pts}] for each predicted game."""
    soup = BeautifulSoup(html, "lxml")
    if "Sorry, no games" in soup.get_text():
        return []
    table = soup.find("table", id="fanmatch-table") or (soup.find_all("table") or [None])[0]
    if table is None:
        return []
    games = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        game = tds[0].get_text(" ", strip=True)
        pred = tds[1].get_text(" ", strip=True)
        m = _PRED.match(pred)
        if not m:
            continue
        game = game.split(" MVP:")[0]
        game = re.sub(r"\s+([A-Za-z0-9]{2,}-T|NCAA|NIT|CBI|CIT)$", "", game)
        sep = " vs. " if " vs. " in game else " at "
        if sep not in game:
            continue
        a, b = game.split(sep, 1)
        strip_rank = lambda s: re.sub(r"^\d+\s+", "", s.strip())
        games.append(
            {
                "team_a": _clean_team(strip_rank(a)),
                "team_b": _clean_team(strip_rank(b)),
                "neutral": sep == " vs. ",
                "winner": _clean_team(m["winner"]),
                "winner_pts": float(m["w"]),
                "loser_pts": float(m["l"]),
            }
        )
    return games


async def _fetch(email: str | None, password: str | None, dates: list[dt.date], log) -> tuple[str, dict]:
    from playwright.async_api import async_playwright

    fanmatch_html = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        page = await browser.new_page(user_agent=UA)
        await page.goto("https://kenpom.com/", timeout=60000)
        for _ in range(10):  # wait out the bot check
            if "moment" not in (await page.title()).lower():
                break
            await page.wait_for_timeout(3000)
        ratings_html = await page.content()

        if email and password:
            try:
                await page.fill("#login input[name=email]", email)
                await page.fill("#login input[name=password]", password)
                await page.click("#login input[name=submit]")
                await page.wait_for_load_state("domcontentloaded")
                await page.goto("https://kenpom.com/", timeout=60000)
                if "Logged in as" not in await page.content() and "logout" not in (await page.content()).lower():
                    log("KenPom login did not succeed; using public ratings instead of FanMatch.")
                else:
                    log("KenPom login OK; using FanMatch predictions.")
                    for d in dates:
                        await page.goto(f"https://kenpom.com/fanmatch.php?d={d.isoformat()}", timeout=60000)
                        await page.wait_for_timeout(1500)
                        fanmatch_html[d] = await page.content()
            except Exception as e:
                log(f"KenPom FanMatch fetch failed ({e}); using public ratings.")
        await browser.close()
    return ratings_html, fanmatch_html


def load(email: str | None, password: str | None, dates: list[dt.date], log=print) -> dict:
    """Returns {"ratings": {...}, "fanmatch": {date: [games]}}. Never raises; KenPom is optional."""
    try:
        ratings_html, fm_html = asyncio.run(_fetch(email, password, dates, log))
    except Exception as e:
        log(f"KenPom unavailable ({e}); continuing with Torvik only.")
        return {"ratings": {}, "fanmatch": {}}
    ratings = parse_ratings(ratings_html)
    if not ratings:
        log("KenPom ratings table not found (bot check or layout change).")
    fanmatch = {d: parse_fanmatch(h) for d, h in fm_html.items()}
    return {"ratings": ratings, "fanmatch": fanmatch}
