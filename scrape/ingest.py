"""Shared URL → event-dict ingestion (used by the CLI and the Discord bot).

Hybrid dispatch by domain, with the generic 【label】 parser as default and the
Pydantic AI LLM extractor as the fallback when a deterministic adapter finds
nothing. Adapters and the LLM are imported lazily so importing this module is cheap.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def _emit(progress: Callable[[str], None] | None, msg: str) -> None:
    """Call the optional progress callback, never letting it break ingestion."""
    if progress is None:
        return
    try:
        progress(msg)
    except Exception:  # noqa: BLE001 - progress is best-effort UI only
        log.debug("progress callback failed", exc_info=True)


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


def ingest_url(
    url: str,
    allow_llm: bool = True,
    force_llm: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Ingested:
    """Fetch + structure a URL. Raises if nothing can parse it.

    ``progress`` is an optional callback invoked with a short human-readable
    status at each stage (used by the bot to update its loading message).
    """
    if force_llm:
        from . import llm

        log.info("ingest %s: forced LLM", url)
        _emit(progress, "🤖 Extracting with AI (Vertex)… this can take ~10–30s")
        t = time.perf_counter()
        data = llm.scrape(url)
        log.info(
            "ingest %s: LLM done in %.1fs (%d rounds)",
            url,
            time.perf_counter() - t,
            len(data.get("rounds", [])),
        )
        return Ingested(data, "llm", True)

    scraper = pick_scraper(url)
    adapter = scraper.__module__.rsplit(".", 1)[-1]
    log.info("ingest %s: adapter=%s", url, adapter)
    _emit(progress, f"🧩 Parsing with the **{adapter}** adapter…")
    t = time.perf_counter()
    data = scraper(url)
    log.info(
        "ingest %s: %s parsed in %.1fs (%d rounds, %d perfs)",
        url,
        adapter,
        time.perf_counter() - t,
        len(data.get("rounds", [])),
        len(data.get("performances", [])),
    )

    if empty(data) and allow_llm:
        from . import llm

        log.info("ingest %s: empty via %s -> LLM fallback", url, adapter)
        _emit(
            progress,
            f"🤖 The **{adapter}** adapter found no structured data — "
            "falling back to AI (Vertex)… this can take ~10–30s",
        )
        t = time.perf_counter()
        data = llm.scrape(url)
        log.info(
            "ingest %s: LLM done in %.1fs (%d rounds)",
            url,
            time.perf_counter() - t,
            len(data.get("rounds", [])),
        )
        return Ingested(data, "llm", True)
    return Ingested(data, adapter, False)
