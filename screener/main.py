"""Daily run: pull Torvik + KenPom + sportsbook lines, flag mispriced spreads/totals,
log picks, grade finished games, and rebuild the dashboard.

Usage:
  python -m screener.main                      # normal run (needs ODDS_API_KEY)
  python -m screener.main --odds-file x.json   # use saved Odds API JSON instead of calling the API
  python -m screener.main --now 2026-11-10T15:00:00Z
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from . import dashboard, history, kenpom, matching, model, odds, torvik

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOOKAHEAD_HOURS = 36  # screen games tipping off within this window


def log(msg: str) -> None:
    print(f"[screener] {msg}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--odds-file")
    ap.add_argument("--now")
    ap.add_argument("--no-kenpom", action="store_true")
    ap.add_argument("--hours", type=float, default=float(os.environ.get("LOOKAHEAD_HOURS") or LOOKAHEAD_HOURS))
    args = ap.parse_args(argv)

    cfg = tomllib.loads((ROOT / "config.toml").read_text())
    tz = ZoneInfo(cfg["display"]["timezone"])
    now = (
        dt.datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if args.now
        else dt.datetime.now(dt.timezone.utc)
    )
    today = now.astimezone(tz).date()
    notes: list[str] = []

    def note(msg):
        log(msg)
        notes.append(msg)

    # 1. Torvik schedule + predictions + finals
    games = torvik.load_schedule(torvik.season_year(today))
    log(f"Torvik: {len(games)} games, {sum(g['final'] for g in games)} final")

    # 2. Sportsbook lines
    if args.odds_file:
        raw = json.loads(Path(args.odds_file).read_text())
    else:
        key = os.environ.get("ODDS_API_KEY")
        if not key:
            log("ODDS_API_KEY is not set. Add it as a repository secret.")
            return 1
        oc = cfg["odds"]
        raw = odds.fetch(key, oc["sport"], oc["regions"], oc["bookmakers"], log)
    horizon = now + dt.timedelta(hours=args.hours)
    events = [odds.summarize(e) for e in raw]
    events = [e for e in events if now < e["start"] <= horizon and (e["spread"] is not None or e["total"] is not None)]
    log(f"{len(events)} upcoming games with lines in the next {args.hours:g}h")

    # 3. Match books -> Torvik
    aliases_path = DATA / "aliases.json"
    aliases = json.loads(aliases_path.read_text()) if aliases_path.exists() else {}
    matched, unmatched = matching.match_events(events, games, aliases, tz)
    for ev in unmatched:
        note(f"Unmatched game: {ev['away']} at {ev['home']} (add to data/aliases.json if needed)")

    # 4. KenPom
    # With no games, still check today's FanMatch so a bad KenPom login shows up early.
    dates = sorted({ev["start"].astimezone(tz).date() for ev, _, _ in matched}) or [today]
    kp = {"ratings": {}, "fanmatch": {}}
    if not args.no_kenpom and (matched or os.environ.get("KENPOM_EMAIL")):
        kp = kenpom.load(os.environ.get("KENPOM_EMAIL"), os.environ.get("KENPOM_PASSWORD"), dates, note)
        log(f"KenPom: {len(kp['ratings'])} team ratings, FanMatch days: {len(kp['fanmatch'])}")

    # 5. Build rows (sportsbook home perspective)
    rows = []
    for ev, g, flipped in matched:
        th, ta = (g["away"], g["home"]) if flipped else (g["home"], g["away"])
        tm = g["torvik_margin"]
        local_date = ev["start"].astimezone(tz).date()
        kpp = None
        if kp["fanmatch"].get(local_date):
            kpp = model.kenpom_from_fanmatch(kp["fanmatch"][local_date], th, ta)
        if kpp is None and kp["ratings"]:
            kpp = model.kenpom_from_ratings(kp["ratings"], th, ta, g["neutral"], cfg["model"]["kenpom_hca"])
        row = {
            **ev,
            "local_date": local_date.isoformat(),
            "torvik_home": th,
            "torvik_away": ta,
            "neutral": g["neutral"],
            "flipped": flipped,
            "torvik_margin": None if tm is None else (-tm if flipped else tm),
            "torvik_total": g["torvik_total"],
            "kp_margin": kpp["margin"] if kpp else None,
            "kp_total": kpp["total"] if kpp else None,
            "kp_source": kpp["source"] if kpp else None,
        }
        rows.append(model.evaluate(row, cfg))
    rows.sort(key=lambda r: r["start"])

    # 6. Pick log
    picks_path = DATA / "picks.csv"
    picks = history.load(picks_path)
    added = history.add_and_update(picks, rows, now)
    graded = history.grade(picks, games, cfg["odds"]["grading_price"])
    history.save(picks_path, picks)
    log(f"Picks: {added} new, {graded} graded, {len(picks)} total")

    # 7. Line snapshot log (for future backtesting)
    snap = DATA / "lines_log.csv"
    new_file = not snap.exists()
    with snap.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["run_utc", "event_id", "start_utc", "away", "home", "neutral", "spread", "total",
                        "torvik_margin", "torvik_total", "kp_margin", "kp_total", "kp_source", "books"])
        for r in rows:
            rnd = lambda x: "" if x is None else round(x, 2)
            w.writerow([now.isoformat(timespec="minutes"), r["id"], r["start"].isoformat(), r["away"], r["home"],
                        int(r["neutral"]), rnd(r["spread"]), rnd(r["total"]), rnd(r["torvik_margin"]),
                        rnd(r["torvik_total"]), rnd(r["kp_margin"]), rnd(r["kp_total"]), r["kp_source"] or "",
                        r["books"]])

    # 8. Dashboard
    out = dashboard.render(rows, picks, cfg, now, notes)
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(out)
    log("Dashboard written to docs/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
