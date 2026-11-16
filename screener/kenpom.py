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


async def _wait_for_bot_check(page, seconds: int = 45):
    """KenPom's bot check ("Just a moment...") can appear again after logging in; wait it out."""
    for _ in range(seconds // 3):
        if "moment" not in (await page.title()).lower():
            return
        await page.wait_for_timeout(3000)


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
                await _wait_for_bot_check(page)
                await page.goto("https://kenpom.com/", timeout=60000)
                await _wait_for_bot_check(page)
                after = await page.content()
                if "Logged in as" not in after and "logout" not in after.lower():
                    # Explain why, without ever printing the credentials.
                    text = BeautifulSoup(after, "lxml").get_text(" ", strip=True).lower()
                    hint = ""
                    for phrase in ("incorrect", "invalid", "not found", "expired", "too many", "subscription"):
                        i = text.find(phrase)
                        if i >= 0:
                            hint = text[max(0, i - 60): i + 60]
                            break
                    has_form = await page.locator("#login input[name=password]").count() > 0
                    log("KenPom login did not succeed; using public ratings instead of FanMatch. "
                        f"(page: {await page.title()!r}, login form still shown: {has_form}"
                        + (f", site says: '...{hint}...'" if hint else "") + ")")
                else:
                    log("KenPom login OK; using FanMatch predictions.")
                    for d in dates:
                        await page.goto(f"https://kenpom.com/fanmatch.php?d={d.isoformat()}", timeout=60000)
                        await _wait_for_bot_check(page)
                        await page.wait_for_timeout(1500)
                        fanmatch_html[d] = await page.content()
            except Exception as e:
                log(f"KenPom FanMatch fetch failed ({e}); using public ratings.")
        await browser.close()
    return ratings_html, fanmatch_html


API = "https://kenpom.com/api.php"


def _api_get(key: str, **params):
    import requests

    r = requests.get(API, params=params, headers={"Authorization": f"Bearer {key}", "User-Agent": UA}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]!r}")
    return r.json()


def _season(day: dt.date) -> int:
    return day.year + 1 if day.month >= 8 else day.year


def load_api(key: str, dates: list[dt.date], log=print) -> dict | None:
    """KenPom's official API (Authorization: Bearer <key>). No browser, no bot check.
    FanMatch only serves dates up to today, so later dates fall back to ratings."""
    try:
        rows = _api_get(key, endpoint="ratings", y=_season(min(dates)))
    except Exception as e:
        log(f"KenPom API ratings request failed ({e}); trying the website instead.")
        return None
    ratings = {}
    for r in rows:
        o, d, t = _f(r.get("AdjOE")), _f(r.get("AdjDE")), _f(r.get("AdjTempo"))
        if r.get("TeamName") and None not in (o, d, t):
            ratings[_clean_team(r["TeamName"])] = {"adjo": o, "adjd": d, "adjt": t}
    fanmatch = {}
    for d in dates:
        try:
            games = _api_get(key, endpoint="fanmatch", d=d.isoformat())
        except Exception as e:
            print(f"[screener] KenPom API FanMatch for {d} not available ({str(e)[:80]}); using ratings for that day.", flush=True)
            continue
        out = []
        for g in games or []:
            hp, vp = _f(g.get("HomePred")), _f(g.get("VisitorPred"))
            if hp is None or vp is None or not g.get("Home") or not g.get("Visitor"):
                continue
            home, vis = _clean_team(g["Home"]), _clean_team(g["Visitor"])
            win_home = hp >= vp
            out.append({
                "team_a": vis, "team_b": home, "neutral": False,
                "winner": home if win_home else vis,
                "winner_pts": max(hp, vp), "loser_pts": min(hp, vp),
            })
        fanmatch[d] = out
    print(f"[screener] KenPom API OK: {len(ratings)} team ratings, FanMatch for {sum(1 for v in fanmatch.values() if v)} of {len(dates)} day(s).", flush=True)
    return {"ratings": ratings, "fanmatch": fanmatch}


def load(email: str | None, password: str | None, dates: list[dt.date], log=print, api_key: str | None = None) -> dict:
    """Returns {"ratings": {...}, "fanmatch": {date: [games]}}. Never raises; KenPom is optional.
    Uses the KenPom API when api_key is set; otherwise (or if the API fails) the website."""
    if api_key:
        res = load_api(api_key, dates, log)
        if res is not None and res["ratings"]:
            return res
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
