"""Offline tests for the Pydantic AI fallback extractor.

No LLM is called — a fake agent (exposing .run_sync().output) returns a canned
ExtractedEvent, exercising normalisation + the end-to-end schema round-trip.
"""

import yaml

from schema.models import Event
from scrape.ingest import empty as _empty
from scrape.llm import (
    ExtractedEvent,
    ExtractedPerformance,
    ExtractedRound,
    _to_event_dict,
    extract_event,
    page_text,
)
from scrape.util import to_event_yaml

SAMPLE = ExtractedEvent(
    name="Test Event 9月公演",
    artist="TestBand",
    kind="concert",
    series=["Indie"],
    performances=[
        ExtractedPerformance(
            date="2026-09-07T00:00:00", venue="下北沢シャングリラ", doors="18:15", starts="19:00"
        ),
    ],
    rounds=[
        ExtractedRound(
            name="FC先行",
            type="presale",
            apply_open="2026-06-05T12:00:00",
            apply_deadline="2026-06-21T23:59:00",
            source_quote="受付期間：2026年6月5日(金)12:00～6月21日(日)23:59",
        ),
    ],
)


class _Result:
    def __init__(self, output):
        self.output = output


class _Agent:
    def __init__(self, output):
        self._output = output

    def run_sync(self, prompt):
        assert "PAGE TEXT" in prompt  # the built prompt is passed through
        return _Result(self._output)


def test_to_event_dict_normalises():
    d = _to_event_dict(SAMPLE, "https://example.jp/1")
    assert d["source_url"] == "https://example.jp/1"
    assert d["kind"] == "concert"
    assert d["performances"][0]["date"] == "2026-09-07"  # time stripped
    assert "src:" in d["rounds"][0]["notes"]  # source_quote folded in
    assert "source_quote" not in d["rounds"][0]


def test_extract_event_roundtrips_through_schema():
    data = extract_event("page text", "https://example.jp/1", "Title", agent=_Agent(SAMPLE))
    raw = yaml.safe_load(to_event_yaml(data))
    raw["id"] = "2026-test-llm"
    ev = Event.model_validate(raw)  # LLM draft is valid in our schema
    assert ev.artist == "TestBand" and ev.source_url.endswith("/1")
    assert ev.rounds[0].apply_deadline.isoformat() == "2026-06-21T23:59:00+09:00"
    assert ev.rounds[0].notes and "src:" in ev.rounds[0].notes


def test_page_text_strips_chrome():
    html = (
        "<html><head><title>X｜公式FC</title></head><body>"
        "<nav>HOME INFORMATION</nav><p>本文 2026年9月7日</p>"
        "<footer>プライバシー</footer></body></html>"
    )
    title, text = page_text(html)
    assert "X" in title
    assert "本文" in text and "HOME" not in text and "プライバシー" not in text


def test_empty_predicate_triggers_fallback():
    assert _empty({"name": "x", "rounds": [], "performances": []}) is True
    assert _empty({"rounds": [{"name": "r"}]}) is False
