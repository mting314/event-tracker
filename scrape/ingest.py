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


# Max nested links to follow out of an X post, and how deep to recurse. An X post
# rarely links more than one real event page; the cap bounds cost and keeps a
# spam-heavy post from fanning out into many fetches.
_MAX_FOLLOW = 3
_MAX_DEPTH = 2


def _merge_x_link(x_data: dict, link_data: dict, link_url: str) -> dict:
    """Fold the X post's date hints into the (richer) linked-page result.

    The linked official/FC page is authoritative for name/venue/rounds; the post
    only contributes a source link and, if the page parse came up thin, its dates.
    """
    out = dict(link_data)
    if not out.get("source_url"):
        out["source_url"] = link_url
    if not (out.get("performances") or out.get("event_dates")) and x_data.get("event_dates"):
        out["event_dates"] = x_data["event_dates"]
    if not out.get("rounds") and x_data.get("rounds"):
        out["rounds"] = x_data["rounds"]
    return out


def _content_score(data: dict) -> tuple[int, int]:
    """Rank a result by how much real event content it carries (rounds, then shows)."""
    return (len(data.get("rounds") or []), len(data.get("performances") or []))


def _follow_x_links(links, allow_llm, progress, depth):
    """Follow links found in an X post; return (link, Ingested) of the best, or None.

    Pass 1 follows every candidate with the **LLM off** and keeps the richest
    deterministic result. This is deliberate: an X ticket post often links several
    pages (e.g. a movie page *and* the ticket page), and an empty deterministic
    parse would otherwise trigger the LLM to fabricate a plausible-but-wrong event
    from a tangential link — which then wins by being "first". Letting the page
    with the most rounds win picks the ticket page over a movie/screening page.
    Only when nothing parses deterministically do we let the LLM try, in priority
    order (source_links is already sorted so ticket/live links come first).
    """
    candidates = list(links)[:_MAX_FOLLOW]
    if not candidates:
        return None

    deterministic = []
    for link in candidates:
        _emit(progress, f"🔗 Following link from the X post: {link} …")
        try:
            sub = _ingest_url(
                link, allow_llm=False, force_llm=False, progress=progress, _depth=depth + 1
            )
        except Exception as exc:  # noqa: BLE001 - try the next link
            log.warning("ingest: nested link %s failed (%s)", link, exc)
            continue
        if not empty(sub.data):
            deterministic.append((link, sub))
    if deterministic:
        return max(deterministic, key=lambda ls: _content_score(ls[1].data))

    if allow_llm:
        for link in candidates:  # priority order; stop at the first that yields anything
            _emit(progress, f"🤖 No structured page found — trying AI on {link} …")
            try:
                sub = _ingest_url(
                    link, allow_llm=True, force_llm=True, progress=progress, _depth=depth + 1
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("ingest: nested link %s (LLM) failed (%s)", link, exc)
                continue
            if not empty(sub.data):
                return link, sub
    return None


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
    """Fetch + structure a URL, then backfill English labels. Raises if nothing parses.

    ``progress`` is an optional callback invoked with a short human-readable status
    at each stage (used by the bot to update its loading message).

    Deterministic adapters emit only the source-language names, while the LLM
    extractor also returns ``name_en``. To keep EN/JA display consistent across
    sources, a deterministic result gets a cheap translate-only LLM pass to fill any
    missing ``name_en`` (best-effort — skipped silently if the LLM is unavailable).
    """
    res = _ingest_url(url, allow_llm, force_llm, progress, _depth=0)
    if allow_llm and not res.used_llm:
        try:
            from . import llm

            res.data = llm.translate_event(res.data)
        except Exception as exc:  # noqa: BLE001 - English labels are best-effort
            log.warning("ingest %s: English backfill skipped (%s)", url, exc)
    return res


def _ingest_url(
    url: str,
    allow_llm: bool = True,
    force_llm: bool = False,
    progress: Callable[[str], None] | None = None,
    _depth: int = 0,
) -> Ingested:
    """Core dispatch (no English backfill — that's added once by ``ingest_url``)."""

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

    # X post: the details usually live behind a link in the post, not in the post
    # text. Follow the nested links and merge the best result back (the post is the
    # trigger; the page is the truth).
    if adapter == "x_post" and _depth < _MAX_DEPTH and data.get("source_links"):
        followed = _follow_x_links(data["source_links"], allow_llm, progress, _depth)
        if followed:
            link, sub = followed
            log.info("ingest %s: followed nested link %s via %s", url, link, sub.adapter)
            merged = _merge_x_link(data, sub.data, link)
            return Ingested(merged, f"x→{sub.adapter}", sub.used_llm)

    # Domain adapter found nothing usable -> try the LLM (generic already tried it above).
    if empty(data) and allow_llm and adapter != "generic":
        try:
            return _llm(f"empty via {adapter}")
        except Exception as exc:  # noqa: BLE001
            log.warning("ingest %s: LLM fallback failed (%s)", url, exc)
    return Ingested(data, adapter, False)
