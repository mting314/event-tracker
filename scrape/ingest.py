"""Shared URL → event-dict ingestion (used by the CLI and the Discord bot).

Hybrid dispatch by domain. Domain-tuned adapters (official lovelive-anime.jp,
eventernote, x) run deterministically with an LLM fallback only if they find
nothing. Arbitrary (non-domain) pages go **LLM-first** — the generic 【label】
parser is best-effort and routinely mangles real ticket pages (per-day lottery
rounds, per-day apply URLs), so it's only a fallback when the LLM is unavailable.
Adapters and the LLM are imported lazily so importing this module is cheap.
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
    from . import eventernote, generic, llfans, official, x_post

    host = urlparse(url).netloc.lower()
    if "ll-fans.jp" in host:
        return llfans.scrape
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

    def _llm(reason: str) -> Ingested:
        from . import llm

        log.info("ingest %s: LLM (%s)", url, reason)
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

    if force_llm:
        return _llm("forced")

    scraper = pick_scraper(url)
    adapter = scraper.__module__.rsplit(".", 1)[-1]

    # The generic 【label】 parser is best-effort and routinely mangles real ticket
    # pages (per-day lottery rounds, per-day apply URLs), so for arbitrary (non-
    # domain) pages prefer the LLM. The parser stays as a fallback if the LLM is
    # unavailable (no GCP creds). Domain adapters (official/eventernote/x) are
    # trusted and stay deterministic, with an LLM fallback only when they find nothing.
    if adapter == "generic" and allow_llm:
        try:
            return _llm("generic page")
        except Exception as exc:  # noqa: BLE001 - fall back to the deterministic parser
            log.warning("ingest %s: LLM failed (%s); using generic parser", url, exc)
            _emit(progress, "⚠️ AI unavailable — using the basic parser…")

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

    # Domain adapter found nothing usable -> try the LLM (generic already tried it above).
    if empty(data) and allow_llm and adapter != "generic":
        try:
            return _llm(f"empty via {adapter}")
        except Exception as exc:  # noqa: BLE001
            log.warning("ingest %s: LLM fallback failed (%s)", url, exc)
    return Ingested(data, adapter, False)
