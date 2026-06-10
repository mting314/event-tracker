"""Generic parser for the common Japanese fan-club / live-house page format.

Many artist FC sites (e.g. lustqueen.info and other hosted-FC platforms) lay out
events as 【label】value blocks:

    公演概要①
    【タイトル】 …
    【公演日程】 2026年9月7日(月) 開場 18:15 / 開演 19:00
    【会場】     東京・下北沢シャングリラ / 〒155-…
    ■… チケット先行受付期間 ※抽選※
    2026年6月5日(金)12:00～6月21日(日)23:59

This is best-effort and deterministic (no LLM); the hybrid dispatcher falls back
to the LLM extractor for pages this doesn't fit.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .util import parse_date, parse_jp_range

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}
_ROUND_KW = ("受付期間", "申込期間", "受付開始", "先行受付", "抽選受付", "発売期間")


def _field(block: str, label: str) -> str | None:
    """Text following 【label】 up to the next 【 / ■ / 《 / separator."""
    m = re.search(r"【" + label + r"】\s*(.+?)(?=【|■|《|-{4,}|\Z)", block, re.S)
    return m.group(1).strip() if m else None


def _time_after(text: str, kw: str) -> str | None:
    m = re.search(kw + r"\s*(\d{1,2}:\d{2})", text or "")
    return m.group(1) if m else None


def _venue_split(text: str | None):
    if not text:
        return None, None
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    venue = parts[0] if parts else None
    addr = next((p for p in parts[1:] if "〒" in p or "都" in p or "県" in p or "区" in p), None)
    return venue, addr


def parse_generic(html: str, url: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:title")
    raw_title = (og.get("content") if og else None) or (
        soup.title.get_text(strip=True) if soup.title else ""
    )
    name = re.split(r"\s*[|｜]\s*", raw_title)[0].strip()
    am = re.match(r"([^\s「『（(]+)", name)
    artist = am.group(1) if am else None

    for t in soup(["script", "style"]):
        t.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]
    full = "\n".join(lines)

    # --- performances from 公演概要 blocks ---
    perfs = []
    blocks = re.split(r"公演概要[\s①-⑳0-9]*", full)
    for b in blocks[1:]:
        b = re.split(r"-{4,}", b)[0]
        sched = _field(b, "公演日程")
        d = parse_date(sched) if sched else None
        if not d:
            continue
        venue, addr = _venue_split(_field(b, "会場"))
        perfs.append(
            {
                "date": d,
                "label": (_field(b, "タイトル") or "").split("\n")[0].strip() or None,
                "venue": venue,
                "venue_address": addr,
                "doors": _time_after(sched, "開場"),
                "starts": _time_after(sched, "開演"),
            }
        )

    # --- rounds: lottery / presale windows ---
    rounds, seen = [], set()
    for i, ln in enumerate(lines):
        if not any(k in ln for k in _ROUND_KW):
            continue
        for cand in [ln, *lines[i + 1 : i + 3]]:
            s, e = parse_jp_range(cand)
            if s:
                key = e.isoformat() if e else s.isoformat()
                if key in seen:
                    break
                seen.add(key)
                nm = re.sub(r"[■◆●▼※\s]+", " ", ln).strip()[:70] or "先行受付"
                rounds.append({"name": nm, "type": "presale", "apply_open": s, "apply_deadline": e})
                break

    return {
        "name": name or "TODO event name",
        "artist": artist,
        "kind": "concert",
        "source_url": url,
        "performances": perfs,
        "rounds": rounds,
    }


def fetch(url: str) -> str:
    import requests

    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def scrape(url: str) -> dict:
    return parse_generic(fetch(url), url)
