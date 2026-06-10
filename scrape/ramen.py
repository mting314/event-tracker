"""Discover Love Live events + their official URLs from ramen.events.

ramen.events is the index: every event post links the authoritative official
live page (lovelive-anime.jp). This crawls the Ghost sitemap, pulls out the
official URLs, and can flag *untracked* events that already have upcoming lottery
rounds — i.e. events worth adding.

    python -m scrape.ramen                # list slug -> official URL (✓ = tracked)
    python -m scrape.ramen --candidates   # untracked events with upcoming rounds
"""

from __future__ import annotations

import argparse
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from schema.models import JST, load_all_events

from .official import scrape as scrape_official
from .util import EVENTS_DIR

SITEMAP = "https://ramen.events/sitemap-posts.xml"
UA = {"User-Agent": "Mozilla/5.0 (compatible; ll-lottery-tracker/0.1)"}
_OFF_RE = re.compile(r"https?://(?:www\.)?lovelive-anime\.jp/[^\s\"'<>)]*live_detail[^\s\"'<>)]*")


def _canonical(url: str) -> str:
    url = url.rstrip(".,)")
    if "//lovelive-anime" in url and "//www." not in url:
        url = url.replace("//lovelive-anime", "//www.lovelive-anime")
    return url


def discover_posts() -> list[str]:
    xml = requests.get(SITEMAP, headers=UA, timeout=20).text
    return re.findall(r"<loc>(.*?)</loc>", xml)


def post_official(url: str) -> tuple[str, str | None]:
    slug = url.rstrip("/").split("/")[-1]
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style"]):
            t.decompose()
        m = _OFF_RE.search(soup.get_text(" "))
        return slug, (_canonical(m.group(0)) if m else None)
    except Exception:  # noqa: BLE001 - skip unreachable posts
        return slug, None


def discover_official(max_workers: int = 12) -> list[tuple[str, str]]:
    """[(slug, official_url)] deduped by official URL, sorted by slug."""
    rows = list(ThreadPoolExecutor(max_workers=max_workers).map(post_official, discover_posts()))
    seen: dict[str, str] = {}
    for slug, off in rows:
        if off and off not in seen:
            seen[off] = slug
    return sorted(((s, o) for o, s in seen.items()), key=lambda x: x[0])


def _jst(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=JST)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scrape.ramen")
    ap.add_argument(
        "--candidates", action="store_true", help="show untracked events that have upcoming rounds"
    )
    ap.add_argument("--today", help="YYYY-MM-DD reference date (default: now)")
    args = ap.parse_args(argv)
    today = (
        datetime.fromisoformat(args.today).replace(tzinfo=JST) if args.today else datetime.now(JST)
    )

    tracked = {e.official_url for e in load_all_events(EVENTS_DIR) if e.official_url}
    disc = discover_official()
    print(
        f"discovered {len(disc)} official live URLs from ramen.events "
        f"({sum(1 for _, o in disc if o in tracked)} tracked)"
    )

    if not args.candidates:
        for slug, off in disc:
            print(f" [{'✓' if off in tracked else ' '}] {slug[:38]:38s} {off}")
        return 0

    untracked = [(s, o) for s, o in disc if o not in tracked]

    def check(item):
        slug, off = item
        try:
            d = scrape_official(off)
            fut = [
                r
                for r in d["rounds"]
                if r.get("apply_deadline") and _jst(r["apply_deadline"]) > today
            ]
            soon = min((r["apply_deadline"] for r in fut), default=None)
            return slug, off, len(d["rounds"]), len(fut), soon
        except Exception:  # noqa: BLE001
            return slug, off, -1, 0, None

    res = [r for r in ThreadPoolExecutor(max_workers=10).map(check, untracked) if r[3] > 0]
    res.sort(key=lambda r: r[4])
    print(f"\n=== {len(res)} untracked events with UPCOMING rounds ===")
    for slug, off, nr, nf, soon in res:
        print(f"  next {str(soon)[:16]}  {slug[:36]:36s} rounds={nr} future={nf}\n      {off}")
    print("\nAdd one: copy its official_url into a new events/<slug>.yaml (or sources.yaml).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
