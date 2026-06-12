"""Tests for ingest_url dispatch — the generic→LLM-first policy.

Generic (non-domain) pages prefer the LLM because the 【label】 parser routinely
mangles real ticket pages; domain adapters stay deterministic with LLM-on-empty.

ingest_url derives the adapter name from the scraper's ``__module__``, so each
patched scraper mock must carry the real module name.
"""

from unittest.mock import patch

from scrape import ingest

GEN_URL = "https://lustqueen.info/news/detail/1"  # -> generic adapter
OFF_URL = "https://www.lovelive-anime.jp/x/live_detail.php?p=1"  # -> official adapter


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
