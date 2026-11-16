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
APPLE_ID = "1567990327"  # Apple Podcasts id of the same show (audio fallback)
AUDIO_MAX_AGE_HOURS = 30  # audio is slow to transcribe, so only the newest episodes
AUDIO_MAX_EPISODES = 2
WHISPER_MODEL = "base.en"


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


def audio_episodes() -> list[dict]:
    """Newest episodes from the show's podcast RSS feed (found through Apple's lookup API)."""
    feed = requests.get(f"https://itunes.apple.com/lookup?id={APPLE_ID}", timeout=30).json()["results"][0]["feedUrl"]
    r = requests.get(feed, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    from email.utils import parsedate_to_datetime

    eps = []
    for item in ET.fromstring(r.content).iter("item"):
        enc = item.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        pub = parsedate_to_datetime(item.findtext("pubDate"))
        guid = re.sub(r"[^A-Za-z0-9_-]", "", (item.findtext("guid") or enc.get("url")))[-24:]
        eps.append({"id": guid, "title": (item.findtext("title") or "").strip(), "published": pub,
                    "audio": enc.get("url"), "url": item.findtext("link") or enc.get("url")})
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=AUDIO_MAX_AGE_HOURS)
    return sorted([e for e in eps if e["published"] >= cutoff], key=lambda e: e["published"], reverse=True)


def transcribe_audio(url: str) -> str:
    import tempfile

    from faster_whisper import WhisperModel

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ep.mp3"
        with requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=120) as r:
            r.raise_for_status()
            with path.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        segs, info = model.transcribe(str(path), vad_filter=True, beam_size=1)
        lines = []
        for s in segs:
            t = int(s.start)
            lines.append(f"[{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}] {s.text.strip()}")
        log(f"transcribed {info.duration / 60:.0f} min of audio")
        return "\n".join(lines)


def _et_date(ts: dt.datetime) -> str:
    from zoneinfo import ZoneInfo

    return ts.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def _save(ep: dict, text: str, source: str) -> None:
    name = f"{_et_date(ep['published'])}_{ep['id']}.txt"
    header = json.dumps({"video_id": ep["id"], "title": ep["title"], "published_utc": ep["published"].isoformat(),
                         "url": ep.get("url") or f"https://www.youtube.com/watch?v={ep['id']}", "source": source})
    (OUT / name).write_text(header + "\n\n" + text + "\n")
    log(f"saved {name} ({len(text):,} chars, from {source}): {ep['title']}")


def _have(ep_id: str) -> bool:
    return any(OUT.glob(f"*_{ep_id}.txt"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    got_youtube = False
    try:
        eps = recent_episodes()
        log(f"YouTube: {len(eps)} episode(s) in the last {MAX_AGE_DAYS} days")
        for ep in eps:
            if _have(ep["id"]):
                continue
            try:
                _save(ep, transcript(ep["id"]), "youtube captions")
                got_youtube = True
            except Exception as e:
                log(f"YouTube transcript unavailable for {ep['title']!r}: {str(e)[:160]}")
                if "not a bot" in str(e):
                    log("YouTube is blocking this server; skipping the rest of YouTube.")
                    break
    except Exception as e:
        log(f"could not read the YouTube playlist feed: {e}")
    if got_youtube:
        return 0
    # YouTube often blocks GitHub's servers, so fall back to the audio podcast + speech-to-text.
    try:
        eps = audio_episodes()
    except Exception as e:
        log(f"could not read the audio podcast feed: {e}")
        return 0
    log(f"Audio feed: {len(eps)} episode(s) in the last {AUDIO_MAX_AGE_HOURS}h")
    done = 0
    for ep in eps:
        if done >= AUDIO_MAX_EPISODES or _have(ep["id"]):
            continue
        try:
            _save(ep, transcribe_audio(ep["audio"]), "audio transcription")
            done += 1
        except Exception as e:
            log(f"audio transcription failed for {ep['title']!r}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
