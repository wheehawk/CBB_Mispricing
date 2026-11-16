"""Podcast locks ("The College Experience: Basketball") for the screener.

data/podcast_picks.json is written by a Claude scheduled task from the transcripts that
screener/podcast_feed.py saves. Format:

{"episodes": {"<video id>": {
    "title": "...", "url": "...", "episode_date": "YYYY-MM-DD", "hosts": ["A", "B", "C"],
    "games": [{"date": "YYYY-MM-DD", "away": "<Torvik name>", "home": "<Torvik name>",
               "pick": "<Torvik name of the team picked>", "category": "3/3 lock" | "2/3 lock" | "2/2 agree",
               "hosts": ["A", "B"], "line": "-3.5"}]}}}

Team names use Torvik's spelling, so games match the screener rows exactly.
"""
from __future__ import annotations

import json
from pathlib import Path

ORDER = {"3/3 lock": 0, "2/3 lock": 1, "2/2 agree": 2}


def load(path: Path, log=print) -> dict:
    """{(date, frozenset({away, home})): game entry}. Never raises."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log(f"Podcast picks file unreadable ({e}); skipping podcast column.")
        return {}
    out = {}
    for ep in data.get("episodes", {}).values():
        for g in ep.get("games", []):
            if g.get("category") not in ORDER:
                continue
            key = (g["date"], frozenset((g["away"], g["home"])))
            old = out.get(key)
            if old is None or ORDER[g["category"]] < ORDER[old["category"]]:
                out[key] = {**g, "episode": ep.get("title", ""), "url": ep.get("url", "")}
    return out


def for_row(pods: dict, row: dict):
    """Podcast entry for a screener row, with which side it is on ("home"/"away"), or None."""
    g = pods.get((row["local_date"], frozenset((row["torvik_home"], row["torvik_away"]))))
    if not g:
        return None
    side = "home" if g["pick"] == row["torvik_home"] else "away" if g["pick"] == row["torvik_away"] else None
    if side is None:
        return None
    return {"category": g["category"], "side": side, "team": g["pick"], "hosts": g.get("hosts", []),
            "line": g.get("line", ""), "label": f"{g['category']}: {g['pick']}"}
