"""Tests for the watcher's per-source adapter dispatch."""

from unittest.mock import patch

import pytest

from scrape import watch
from scrape.ingest import Ingested


def test_unknown_adapter_raises():
    with pytest.raises(ValueError, match="unknown adapter"):
        watch.scrape_source("https://example.jp", "bogus")


def test_explicit_official_adapter_calls_official():
    with patch.object(watch.official, "scrape", return_value={"name": "X", "rounds": []}) as m:
        out = watch.scrape_source("https://www.lovelive-anime.jp/x", "official")
    m.assert_called_once_with("https://www.lovelive-anime.jp/x")
    assert out["name"] == "X"


def test_explicit_generic_adapter_calls_generic():
    with patch.object(watch.generic, "scrape", return_value={"name": "G", "rounds": []}) as m:
        out = watch.scrape_source("https://lustqueen.info/news/1", "generic")
    m.assert_called_once()
    assert out["name"] == "G"


def test_auto_uses_shared_dispatch():
    fake = Ingested({"name": "Y", "rounds": []}, "generic", False)
    with patch.object(watch, "ingest_url", return_value=fake) as m:
        out = watch.scrape_source("https://lustqueen.info/news/1")  # adapter None -> auto
    m.assert_called_once_with("https://lustqueen.info/news/1")
    assert out["name"] == "Y"


def test_llm_adapter_forces_llm():
    fake = Ingested({"name": "Z", "rounds": []}, "llm", True)
    with patch.object(watch, "ingest_url", return_value=fake) as m:
        watch.scrape_source("https://band.example/news/1", "llm")
    _, kwargs = m.call_args
    assert kwargs.get("force_llm") is True
