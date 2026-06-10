"""Shared URL → event-dict ingestion (used by the CLI and the Discord bot).

Hybrid dispatch by domain, with the generic 【label】 parser as default and the
Pydantic AI LLM extractor as the fallback when a deterministic adapter finds
nothing. Adapters and the LLM are imported lazily so importing this module is cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


def pick_scraper(url: str):
    """Hybrid dispatch by domain; the generic 【label】 parser is the default."""
    from . import eventernote, generic, official, x_post

    host = urlparse(url).netloc.lower()
    if "lovelive-anime.jp" in host:
        return official.scrape
    if "eventernote.com" in host:
        return eventernote.scrape
    if "x.com" in host or "twitter.com" in host:
        return x_post.scrape
    return generic.scrape


def empty(data: dict) -> bool:
    """An adapter result worth falling back to the LLM for."""
    return not (data.get("rounds") or data.get("performances") or data.get("event_dates"))


@dataclass
class Ingested:
    data: dict
    adapter: str  # 'official' | 'generic' | 'llm' | ...
    used_llm: bool


def ingest_url(url: str, allow_llm: bool = True, force_llm: bool = False) -> Ingested:
    """Fetch + structure a URL. Raises if nothing can parse it."""
    if force_llm:
        from . import llm

        return Ingested(llm.scrape(url), "llm", True)

    scraper = pick_scraper(url)
    adapter = scraper.__module__.rsplit(".", 1)[-1]
    data = scraper(url)

    if empty(data) and allow_llm:
        from . import llm

        return Ingested(llm.scrape(url), "llm", True)
    return Ingested(data, adapter, False)
