"""Best-effort scraper for official / ticketing pages (eplus, Lawson, etc.).

These pages have no common structure, so this only *suggests* round dates by
pulling date+time spans out of the visible text near lottery keywords. Always
treat the output as a draft to verify by hand.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .util import parse_datetime

# Keywords that hint a line is about a lottery/sale window.
KEYWORDS = ("先行", "抽選", "受付", "申込", "一般発売", "販売", "presale", "lottery")
UA = "Mozilla/5.0 (compatible; ll-lottery-tracker/0.1; +https://github.com/)"


def parse_ticket(html: str, url: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)

    suggestions = []
    for line in text.splitlines():
        if not any(k in line for k in KEYWORDS):
            continue
        dt = parse_datetime(line)
        if dt:
            suggestions.append({"name": line[:60], "apply_deadline": dt})

    name = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        name = og["content"].strip()

    return {
        "name": name,
        "official_url": url,
        "rounds": suggestions,
        "notes": "TODO verify scraped round dates against the source page",
    }


def fetch(url: str) -> str:
    import requests

    resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def scrape(url: str) -> dict:
    return parse_ticket(fetch(url), url)
