"""Download transcripts of new "The College Experience: Basketball" episodes from YouTube.

Runs inside the GitHub Action (this needs YouTube access). Saves each new episode's transcript to
data/podcast/transcripts/<published date>_<video id>.txt. A Claude scheduled task then reads new
transcripts and writes the hosts' locks to data/podcast_picks.json, which the screener shows.

    python -m screener.podcast_feed            # newest episodes, skip ones already saved
Never fails the screener run: problems are printed and it exits 0.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

PLAYLIST_ID = "PL0NOursq2ZyZXQFqwOwfZauxpYq_gMFQx"
FEED = f"https://www.youtube.com/feeds/videos.xml?playlist_id={PLAYLIST_ID}"
OUT = Path(__file__).resolve().parent.parent / "data" / "podcast" / "transcripts"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MAX_AGE_DAYS = 3  # only look at recent episodes


def log(msg):
    print(f"[podcast_feed] {msg}", flush=True)


def recent_episodes() -> list[dict]:
    r = requests.get(FEED, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    eps = []
    for e in ET.fromstring(r.content).findall("a:entry", ns):
        vid = e.findtext("yt:videoId", namespaces=ns)
        pub = dt.datetime.fromisoformat(e.findtext("a:published", namespaces=ns).replace("Z", "+00:00"))
        eps.append({"id": vid, "title": e.findtext("a:title", namespaces=ns), "published": pub})
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=MAX_AGE_DAYS)
    return sorted([e for e in eps if e["published"] >= cutoff], key=lambda e: e["published"], reverse=True)


def _via_api(vid: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    fetched = api.fetch(vid, languages=["en", "en-US"])
    lines = []
    for s in fetched:
        t = int(s.start)
        lines.append(f"[{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}] {s.text.strip()}")
    return "\n".join(lines)


def _via_ytdlp(vid: str) -> str:
    import tempfile

    import yt_dlp

    with tempfile.TemporaryDirectory() as d:
        opts = {"skip_download": True, "writeautomaticsub": True, "writesubtitles": True,
                "subtitleslangs": ["en.*", "en"], "subtitlesformat": "vtt", "quiet": True,
                "outtmpl": f"{d}/%(id)s.%(ext)s"}
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([f"https://www.youtube.com/watch?v={vid}"])
        files = sorted(Path(d).glob("*.vtt"))
        if not files:
            raise RuntimeError("no captions available yet")
        out, last = [], None
        for line in files[0].read_text().splitlines():
            if "-->" in line or not line.strip() or line.startswith(("WEBVTT", "Kind:", "Language:")):
                continue
            text = re.sub(r"<[^>]+>", "", line).strip()
            if text and text != last:  # auto-captions repeat each line
                out.append(text)
                last = text
        return "\n".join(out)


def transcript(vid: str) -> str:
    errors = []
    for fn in (_via_api, _via_ytdlp):
        try:
            text = fn(vid)
            if len(text) > 500:
                return text
            errors.append(f"{fn.__name__}: transcript too short")
        except Exception as e:
            errors.append(f"{fn.__name__}: {str(e).splitlines()[0][:150] if str(e) else type(e).__name__}")
    raise RuntimeError("; ".join(errors))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        eps = recent_episodes()
    except Exception as e:
        log(f"could not read the playlist feed: {e}")
        return 0
    log(f"{len(eps)} episode(s) in the last {MAX_AGE_DAYS} days")
    for ep in eps:
        name = f"{ep['published'].astimezone(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()}_{ep['id']}.txt"
        path = OUT / name
        if path.exists():
            continue
        try:
            text = transcript(ep["id"])
        except Exception as e:
            log(f"no transcript yet for {ep['title']!r} ({ep['id']}): {e}")
            continue
        header = json.dumps({"video_id": ep["id"], "title": ep["title"], "published_utc": ep["published"].isoformat(),
                             "url": f"https://www.youtube.com/watch?v={ep['id']}"})
        path.write_text(header + "\n\n" + text + "\n")
        log(f"saved {name} ({len(text):,} chars): {ep['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
