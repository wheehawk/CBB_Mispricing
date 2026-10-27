"""Save a stored copy of the Torvik schedule to data/torvik_cache.json.

Torvik blocks GitHub's servers, so this runs from a machine Torvik allows
(a home computer or a Claude scheduled task) and the copy is pushed to the repo.
The daily screener uses it automatically when its own Torvik download is blocked.

Usage: python -m screener.torvik_feed
"""
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from . import torvik

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    games = torvik.parse_rows(torvik._get_json(f"{torvik.BASE}/{torvik.season_year(today)}_super_sked.json"))
    n = torvik.write_cache(ROOT / "data" / "torvik_cache.json", games, today)
    print(f"[torvik_feed] saved {n} games around {today} to data/torvik_cache.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
