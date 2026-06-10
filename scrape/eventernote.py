"""Eventernote event-page scraper: URL -> draft event dict.

Eventernote pages list an event's name, date, venue (会場) and cast (出演者),
but **not** lottery application rounds — those stay manual. Parsing is a pure
function over HTML so it can be tested offline.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .util import find_all_dates, parse_date

UA = "Mozilla/5.0 (compatible; ll-lottery-tracker/0.1; +https://github.com/)"


def parse_eventernote(html: str, url: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    # --- name: prefer og:title, then <h1>, then <title> ---
    name = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        name = og["content"].strip()
    if not name and soup.h1:
        name = soup.h1.get_text(strip=True)
    if not name and soup.title:
        name = soup.title.get_text(strip=True)

    # --- cast: anchors linking to /actors/ ---
    performers, seen = [], set()
    for a in soup.select('a[href*="/actors/"]'):
        t = a.get_text(strip=True)
        if t and t not in seen:
            seen.add(t)
            performers.append(t)

    # --- venue: anchor linking to /places/, else label '会場' ---
    venue = None
    place = soup.select_one('a[href*="/places/"]')
    if place:
        venue = place.get_text(strip=True)
    if not venue:
        for label in soup.find_all(string=lambda s: s and "会場" in s):
            sib = getattr(label, "parent", None)
            if sib:
                txt = sib.get_text(" ", strip=True).replace("会場", "").strip("：: ")
                if txt:
                    venue = txt
                    break

    # --- dates: from a date-ish container if present, else whole page ---
    date_scope = soup.select_one(".date, .event_date, time") or soup
    dates = find_all_dates(date_scope.get_text(" ", strip=True))
    if not dates:  # fall back to a single <time datetime=...>
        t = soup.find("time")
        if t and t.get("datetime"):
            d = parse_date(t["datetime"])
            if d:
                dates = [d]

    return {
        "name": name,
        "series": [],  # curator tags this (μ's / Aqours / Liella! / 声優 ...)
        "performers": performers,
        "venue": venue,
        "event_dates": dates,
        "eventernote_url": url,
        "rounds": [],
    }


def fetch(url: str) -> str:
    import requests  # imported lazily so parsing/tests don't require network libs

    resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def scrape(url: str) -> dict:
    return parse_eventernote(fetch(url), url)
