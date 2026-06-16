"""Tests for ingest_url dispatch — the generic→LLM-first policy.

Generic (non-domain) pages prefer the LLM because the 【label】 parser routinely
mangles real ticket pages; domain adapters stay deterministic with LLM-on-empty.

ingest_url derives the adapter name from the scraper's ``__module__``, so each
patched scraper mock must carry the real module name.
"""

from datetime import date
from unittest.mock import patch

from scrape import ingest

GEN_URL = "https://lustqueen.info/news/detail/1"  # -> generic adapter
OFF_URL = "https://www.lovelive-anime.jp/x/live_detail.php?p=1"  # -> official adapter
X_URL = "https://x.com/foo/status/123"  # -> x_post adapter


def test_generic_url_prefers_llm():
    with (
        patch("scrape.llm.scrape", return_value={"name": "X", "rounds": [{"name": "r"}]}) as llm,
        patch("scrape.generic.scrape") as gen,
    ):
        gen.__module__ = "scrape.generic"
        res = ingest.ingest_url(GEN_URL)
    assert res.used_llm and res.adapter == "llm"
    llm.assert_called_once()
    gen.assert_not_called()  # LLM-first; the brittle parser is skipped


def test_generic_url_falls_back_to_parser_when_llm_unavailable():
    with (
        patch("scrape.llm.scrape", side_effect=RuntimeError("no creds")),
        patch("scrape.generic.scrape", return_value={"name": "X", "rounds": []}) as gen,
    ):
        gen.__module__ = "scrape.generic"
        res = ingest.ingest_url(GEN_URL)
    assert not res.used_llm and res.adapter == "generic"
    gen.assert_called_once()


def test_generic_url_without_llm_uses_parser():
    with (
        patch("scrape.generic.scrape", return_value={"name": "X", "rounds": []}) as gen,
        patch("scrape.llm.scrape") as llm,
    ):
        gen.__module__ = "scrape.generic"
        res = ingest.ingest_url(GEN_URL, allow_llm=False)
    assert not res.used_llm and res.adapter == "generic"
    gen.assert_called_once()
    llm.assert_not_called()


def test_official_url_stays_deterministic():
    with (
        patch(
            "scrape.official.scrape", return_value={"name": "X", "rounds": [{"name": "r"}]}
        ) as off,
        patch("scrape.llm.scrape") as llm,
    ):
        off.__module__ = "scrape.official"
        res = ingest.ingest_url(OFF_URL)
    assert not res.used_llm and res.adapter == "official"
    off.assert_called_once()
    llm.assert_not_called()  # trusted adapter — no LLM cost


def test_official_url_empty_falls_back_to_llm():
    with (
        patch("scrape.official.scrape", return_value={"name": "X", "rounds": []}) as off,
        patch("scrape.llm.scrape", return_value={"name": "X", "rounds": [{"name": "r"}]}) as llm,
    ):
        off.__module__ = "scrape.official"
        res = ingest.ingest_url(OFF_URL)
    assert res.used_llm and res.adapter == "llm"
    llm.assert_called_once()


def test_x_post_follows_nested_link_for_details():
    """The X post is thin (a date hint + a link); details come from the link."""
    x_data = {
        "official_url": X_URL,
        "event_dates": [date(2026, 9, 12)],
        "rounds": [],
        "source_links": [OFF_URL],
    }
    link_data = {"name": "Liella! Tour", "rounds": [{"name": "1次先行"}]}
    with (
        patch("scrape.x_post.scrape", return_value=x_data) as xp,
        patch("scrape.official.scrape", return_value=link_data) as off,
        patch("scrape.llm.scrape") as llm,
    ):
        xp.__module__ = "scrape.x_post"
        off.__module__ = "scrape.official"
        res = ingest.ingest_url(X_URL)
    assert res.adapter == "x→official"
    assert res.data["name"] == "Liella! Tour"  # came from the linked page
    assert res.data["source_url"] == OFF_URL  # provenance set to the followed link
    off.assert_called_once()
    llm.assert_not_called()  # link gave details — no LLM needed


def test_x_post_keeps_dates_when_link_is_thin():
    """If the linked page parses empty, keep the post's own date hints + fall back."""
    x_data = {
        "official_url": X_URL,
        "event_dates": [date(2026, 9, 12)],
        "rounds": [{"name": "FC先行"}],
        "source_links": [OFF_URL],
    }
    with (
        patch("scrape.x_post.scrape", return_value=x_data) as xp,
        patch("scrape.official.scrape", return_value={"name": "X", "rounds": []}) as off,
        patch("scrape.llm.scrape", side_effect=RuntimeError("no creds")),
    ):
        xp.__module__ = "scrape.x_post"
        off.__module__ = "scrape.official"
        res = ingest.ingest_url(X_URL)
    # nested link empty + LLM unavailable -> fall through to the post's own hints
    assert res.adapter == "x_post"
    assert res.data["event_dates"] == [date(2026, 9, 12)]
    assert res.data["rounds"] == [{"name": "FC先行"}]


def test_x_post_prefers_ticket_link_over_llm_on_tangential_link():
    """Regression: a ticket post links a movie page *and* the ticket page. The
    movie page parses empty deterministically; the LLM would fabricate a screening
    from it. The richest deterministic result (the ticket page) must win — no LLM."""
    movie = "https://www.lovelive-anime.jp/nijigasaki/movie/Chapter2/"
    ticket = "https://lovelive-anime.jp/nijigasaki/live/live_detail.php?p=8thlive#ticket"
    x_data = {
        "official_url": X_URL,
        "event_dates": [],
        "rounds": [],
        "source_links": [movie, ticket],
    }

    def official_side(url):
        if "live_detail" in url:
            return {"name": "8th Live", "rounds": [{"name": "1次先行"}]}
        return {"name": "movie", "rounds": []}  # movie page: nothing structured

    with (
        patch("scrape.x_post.scrape", return_value=x_data) as xp,
        patch("scrape.official.scrape", side_effect=official_side) as off,
        patch(
            "scrape.llm.scrape",
            return_value={"name": "MOVIE SCREENING", "performances": [{"date": date(2025, 11, 7)}]},
        ) as llm,
    ):
        xp.__module__ = "scrape.x_post"
        off.__module__ = "scrape.official"
        res = ingest.ingest_url(X_URL)  # allow_llm=True (bot default)
    assert res.adapter == "x→official"
    assert res.data["name"] == "8th Live"  # ticket page, not the movie screening
    llm.assert_not_called()  # deterministic ticket result preempts the LLM


def test_x_post_uses_llm_only_when_no_deterministic_link():
    """If every nested link parses empty deterministically, the LLM may try (in
    priority order) — but only as a last resort."""
    fc = "https://someartist.com/fc/event"  # -> generic adapter
    x_data = {"official_url": X_URL, "event_dates": [], "rounds": [], "source_links": [fc]}
    with (
        patch("scrape.x_post.scrape", return_value=x_data) as xp,
        patch("scrape.generic.scrape", return_value={"name": "g", "rounds": []}) as gen,
        patch(
            "scrape.llm.scrape", return_value={"name": "Real Event", "rounds": [{"name": "r"}]}
        ) as llm,
    ):
        xp.__module__ = "scrape.x_post"
        gen.__module__ = "scrape.generic"
        res = ingest.ingest_url(X_URL)
    assert res.used_llm and res.adapter == "x→llm"
    assert res.data["name"] == "Real Event"
    llm.assert_called()


def test_x_post_no_links_falls_back_to_llm():
    """No nested link and a thin post -> existing LLM-on-empty behaviour."""
    x_data = {"official_url": X_URL, "event_dates": [], "rounds": [], "source_links": []}
    with (
        patch("scrape.x_post.scrape", return_value=x_data) as xp,
        patch("scrape.llm.scrape", return_value={"name": "X", "rounds": [{"name": "r"}]}) as llm,
    ):
        xp.__module__ = "scrape.x_post"
        res = ingest.ingest_url(X_URL)
    assert res.used_llm and res.adapter == "llm"
    llm.assert_called_once()


def test_ingest_backfills_english_for_deterministic_result():
    """A deterministic (non-LLM) result gets a translate-only pass to fill name_en."""
    with (
        patch(
            "scrape.official.scrape", return_value={"name": "X", "rounds": [{"name": "r"}]}
        ) as off,
        patch("scrape.llm.translate_event", side_effect=lambda d: {**d, "name_en": "EN"}) as tr,
    ):
        off.__module__ = "scrape.official"
        res = ingest.ingest_url(OFF_URL)
    tr.assert_called_once()
    assert res.data["name_en"] == "EN"


def test_ingest_skips_english_backfill_for_llm_result():
    """An LLM result already carries name_en -> no extra translate pass."""
    with (
        patch("scrape.official.scrape", return_value={"name": "X", "rounds": []}) as off,
        patch("scrape.llm.scrape", return_value={"name": "X", "rounds": [{"name": "r"}]}),
        patch("scrape.llm.translate_event") as tr,
    ):
        off.__module__ = "scrape.official"
        res = ingest.ingest_url(OFF_URL)  # official empty -> LLM fallback
    assert res.used_llm
    tr.assert_not_called()
