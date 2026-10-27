"""Pick log: a play is locked in at the line when it is FIRST flagged, then tracked for
line movement (closing line value) and graded from Torvik final scores."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

FIELDS = [
    "pick_id", "game_date", "start_utc", "away", "home", "torvik_away", "torvik_home",
    "market", "side", "pick_label", "line", "best_line", "best_price", "best_book",
    "edge", "torvik_edge", "kp_edge", "kp_source", "flagged_at", "last_line",
    "status", "units", "final_away", "final_home",
]


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def save(path: Path, picks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for p in picks:
            w.writerow({k: p.get(k, "") for k in FIELDS})


def _r(x):
    return "" if x is None else round(x, 2)


def pick_line(row: dict, market: str, side: str):
    """Consensus line from the pick's own perspective (away spread = -home spread)."""
    if market == "spread":
        return row["spread"] if side == "home" else -row["spread"]
    return row["total"]


def add_and_update(picks: list[dict], rows: list[dict], now: dt.datetime) -> int:
    by_id = {p["pick_id"]: p for p in picks}
    added = 0
    for row in rows:
        for market in ("spread", "total"):
            pid = f"{row['id']}:{market}"
            existing = by_id.get(pid)
            if existing:
                # Track the latest line until tip-off (approximate closing line).
                if row["start"] > now and row[market] is not None:
                    existing["last_line"] = _r(pick_line(row, market, existing["side"]))
                continue
            if not row.get(f"{market}_flag") or row["start"] <= now:
                continue
            side = row[f"{market}_side"]
            line = pick_line(row, market, side)
            best = row["best_" + side]
            team = row["torvik_home"] if side == "home" else row["torvik_away"]
            label = f"{team} {line:+g}" if market == "spread" else f"{side.title()} {line:g}"
            edges = row[f"{market}_edge"]
            p = {
                "pick_id": pid, "game_date": row["local_date"], "start_utc": row["start"].isoformat(),
                "away": row["away"], "home": row["home"],
                "torvik_away": row["torvik_away"], "torvik_home": row["torvik_home"],
                "market": market, "side": side, "pick_label": label, "line": _r(line),
                "best_line": _r(best[0]) if best else "", "best_price": best[1] if best else "",
                "best_book": best[2] if best else "",
                "edge": _r(abs(edges["blend"])), "torvik_edge": _r(edges["torvik"]),
                "kp_edge": _r(edges["kp"]), "kp_source": row.get("kp_source") or "",
                "flagged_at": now.isoformat(timespec="minutes"), "last_line": _r(line),
                "status": "open", "units": "",
            }
            picks.append(p)
            by_id[pid] = p
            added += 1
    return added


def grade(picks: list[dict], games: list[dict], price: int) -> int:
    finals = {(g["date"].isoformat(), g["away"], g["home"]): g for g in games if g["final"]}
    win_units = 100 / abs(price) if price < 0 else price / 100
    graded = 0
    for p in picks:
        if p["status"] != "open":
            continue
        g = None
        for d in (0, -1, 1):
            day = (dt.date.fromisoformat(p["game_date"]) + dt.timedelta(days=d)).isoformat()
            g = finals.get((day, p["torvik_away"], p["torvik_home"])) or finals.get(
                (day, p["torvik_home"], p["torvik_away"])
            )
            if g:
                break
        if not g or g["home_pts"] is None:
            continue
        # Orient Torvik's final score to the sportsbook's home/away labels.
        if g["home"] == p["torvik_home"]:
            fh, fa = g["home_pts"], g["away_pts"]
        else:
            fh, fa = g["away_pts"], g["home_pts"]
        line = float(p["line"])
        if p["market"] == "spread":
            margin = (fh - fa) if p["side"] == "home" else (fa - fh)
            res = margin + line
        else:
            res = (fh + fa - line) * (1 if p["side"] == "over" else -1)
        p["status"] = "win" if res > 0 else "loss" if res < 0 else "push"
        p["units"] = round(win_units if res > 0 else -1.0 if res < 0 else 0.0, 3)
        p["final_away"], p["final_home"] = int(fa), int(fh)
        graded += 1
    return graded


def clv(p: dict):
    """Points of closing line value: positive = market moved toward the pick."""
    try:
        line, last = float(p["line"]), float(p["last_line"])
    except (TypeError, ValueError):
        return None
    if p["market"] == "spread" or p["side"] == "under":
        return round(line - last, 2)
    return round(last - line, 2)
