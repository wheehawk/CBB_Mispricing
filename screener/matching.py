"""Match sportsbook games (Odds API names like "Duke Blue Devils") to Torvik games.

Names alone are ambiguous ("Alabama A&M Bulldogs" contains "Alabama"), so each
betting event is matched against Torvik games on the same date, scoring BOTH teams
together. Manual fixes go in data/aliases.json: {"Odds API name": "Torvik name"}.
"""
from __future__ import annotations

import datetime as dt
import re

from rapidfuzz import fuzz

# Common sportsbook shorthand -> words Torvik uses (applied after normalizing).
SHORTHAND = {
    "uconn": "connecticut",
    "ole miss": "mississippi",
    "pitt": "pittsburgh",
    "nc state": "n c state",
    "umass": "massachusetts",
    "app state": "appalachian state",
    "miami oh": "miami oh",
    "miami ohio": "miami oh",
    "miami": "miami fl",
    "loyola chi": "loyola chicago",
    "loyola md": "loyola md",
    "hawai i": "hawaii",
    "texas a m cc": "texas a m corpus chris",
    "texas a m corpus christi": "texas a m corpus chris",
    "tenn martin": "tennessee martin",
    "ut martin": "tennessee martin",
    "se louisiana": "southeastern louisiana",
    "se missouri state": "southeast missouri state",
    "csu northridge": "cal state northridge",
    "cs northridge": "cal state northridge",
    "csu fullerton": "cal state fullerton",
    "csu bakersfield": "cal state bakersfield",
    "florida int l": "fiu",
    "florida international": "fiu",
    "ul monroe": "louisiana monroe",
    "long island": "liu",
    "miss valley state": "mississippi valley state",
    "mississippi valley": "mississippi valley state",
    "central connecticut state": "central connecticut",
    "omaha": "nebraska omaha",
    "fort wayne": "purdue fort wayne",
    "kansas city": "umkc",
    "texas a m commerce": "east texas a m",
    "sam houston": "sam houston state",
    "grambling": "grambling state",
    "detroit": "detroit mercy",
    "iupui": "iu indy",
    "utrgv": "ut rio grande valley",
}


def norm(name: str, book: bool = False) -> str:
    s = name.lower().replace("&", " ").replace("'", " ").replace("é", "e")
    s = re.sub(r"\(non.?d1\)", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = s.split()
    out = []
    for i, t in enumerate(toks):
        if t == "st":
            t = "saint" if i == 0 else "state"
        elif t == "mt":
            t = "mount"
        elif t == "s" and out:  # possessive leftover: "john s" -> "johns"
            out[-1] += "s"
            continue
        out.append(t)
    s = " ".join(out)
    if book:
        for k in sorted(SHORTHAND, key=len, reverse=True):
            target = SHORTHAND[k]
            if s == target or s.startswith(target + " "):
                break  # already in Torvik form
            if s == k or s.startswith(k + " "):
                s = target + s[len(k):]
                break
    return s


def team_score(book_name: str, torvik_name: str, aliases: dict) -> float:
    """0-100+: how well a sportsbook team name fits a Torvik team name."""
    if aliases.get(book_name) == torvik_name:
        return 200.0
    b, t = norm(book_name, book=True).split(), norm(torvik_name).split()
    if not t:
        return 0.0
    if b[: len(t)] == t:  # Torvik name is the start of the book name (mascot follows)
        return 100.0 + len(t)  # prefer the most specific (longest) match
    core = list(t)
    for suffix in (["state"], ["a", "m"], ["university"]):
        if len(core) > len(suffix) and core[-len(suffix):] == suffix:
            core = core[: -len(suffix)]
            if b[: len(core)] == core:  # books often drop "State" / "A&M" ("McNeese Cowboys")
                return 90.0 + len(core)
    best = 0.0
    for n in (len(t) - 1, len(t), len(t) + 1):
        if n > 0:
            best = max(best, fuzz.ratio(" ".join(b[:n]), " ".join(t)))
    return best * 0.95


def match_events(events: list[dict], games: list[dict], aliases: dict, tz) -> tuple[list[tuple], list[dict]]:
    """Return ([(event, game, flipped)], [unmatched events])."""
    by_date: dict[dt.date, list[dict]] = {}
    for g in games:
        by_date.setdefault(g["date"], []).append(g)
    matched, unmatched, used = [], [], set()
    for ev in events:
        local = ev["start"].astimezone(tz).date()
        cands = by_date.get(local, []) + by_date.get(local - dt.timedelta(days=1), []) + by_date.get(
            local + dt.timedelta(days=1), []
        )
        best, best_s, best_flip = None, 0.0, False
        for g in cands:
            if id(g) in used:
                continue
            s1 = (team_score(ev["away"], g["away"], aliases), team_score(ev["home"], g["home"], aliases))
            s2 = (team_score(ev["away"], g["home"], aliases), team_score(ev["home"], g["away"], aliases))
            for (sa, sh), flip in ((s1, False), (s2, True)):
                if min(sa, sh) >= 80 and sa + sh > best_s:
                    best, best_s, best_flip = g, sa + sh, flip
        if best is None:
            unmatched.append(ev)
        else:
            used.add(id(best))
            matched.append((ev, best, best_flip))
    return matched, unmatched
