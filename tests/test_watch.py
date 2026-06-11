"""Tests for the watcher's per-source adapter dispatch."""

from datetime import date, datetime
from unittest.mock import patch

import pytest

from schema.models import Event
from scrape import watch
from scrape.ingest import Ingested


def _ev(**kw):
    return Event.model_validate({"id": "2026-x", "name": "X", **kw})


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


def test_event_watch_url_prefers_official_then_source():
    assert (
        watch.event_watch_url(_ev(official_url="https://off", source_url="https://src"))
        == "https://off"
    )
    assert watch.event_watch_url(_ev(source_url="https://src")) == "https://src"
    assert watch.event_watch_url(_ev()) is None


def test_event_is_past():
    today = date(2026, 6, 11)
    assert watch.event_is_past(_ev(performances=[{"date": "2030-09-01"}]), today) is False
    assert (
        watch.event_is_past(
            _ev(rounds=[{"name": "r", "apply_deadline": "2030-01-01T00:00"}]), today
        )
        is False
    )
    past = _ev(
        performances=[{"date": "2020-01-01"}],
        rounds=[{"name": "r", "apply_deadline": "2020-01-01T00:00"}],
    )
    assert watch.event_is_past(past, today) is True


def test_auto_watch_scans_unconfigured_future_event(tmp_path):
    ev = _ev(
        id="2026-new",
        name="New",
        official_url="https://off/new",
        performances=[{"date": "2030-09-01"}],
        rounds=[{"name": "old", "apply_deadline": "2030-06-01T23:59:00"}],
    )
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text("sources: []\n")
    parsed = {
        "name": "New",
        "rounds": [{"name": "FC", "apply_deadline": datetime(2030, 7, 1, 23, 59)}],
    }
    with (
        patch.object(watch, "load_all_events", return_value=[ev]),
        patch.object(watch, "scrape_source", return_value=parsed) as ss,
        patch.object(watch, "DRAFTS_DIR", tmp_path / "drafts"),
    ):
        watch.main(["--sources", str(sources_file), "--write"])
    ss.assert_called_once_with("https://off/new", "auto")  # re-scanned via auto dispatch
    draft = tmp_path / "drafts" / "2026-new.rounds.yaml"
    assert draft.exists() and "FC" in draft.read_text()


def test_auto_watch_skips_configured_and_past_events(tmp_path):
    configured = _ev(
        id="2026-cfg", official_url="https://off/cfg", performances=[{"date": "2030-09-01"}]
    )
    past = _ev(
        id="2026-old",
        official_url="https://off/old",
        performances=[{"date": "2020-01-01"}],
        rounds=[{"name": "r", "apply_deadline": "2020-01-01T00:00"}],
    )
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(
        "sources:\n  - id: 2026-cfg\n    url: https://off/cfg\n    adapter: official\n"
    )
    with (
        patch.object(watch, "load_all_events", return_value=[configured, past]),
        patch.object(watch, "scrape_source", return_value={"name": "X", "rounds": []}) as ss,
    ):
        watch.main(["--sources", str(sources_file)])
    # only the configured source is scanned (once); the past event is skipped,
    # and the configured event is NOT re-scanned by the auto pass.
    assert ss.call_count == 1
    assert ss.call_args_list[0].args[0] == "https://off/cfg"
