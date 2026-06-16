"""Offline tests for the Pydantic AI fallback extractor.

No LLM is called — a fake agent (exposing .run_sync().output) returns a canned
ExtractedEvent, exercising normalisation + the end-to-end schema round-trip.
"""

import logging

import yaml

from schema.models import Event
from scrape.ingest import empty as _empty
from scrape.llm import (
    ExtractedEvent,
    ExtractedPerformance,
    ExtractedRound,
    _to_event_dict,
    _usage_str,
    extract_event,
    page_text,
    translate_event,
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


class _Usage:
    def __init__(self, inp, out, total):
        self.input_tokens = inp
        self.output_tokens = out
        self.total_tokens = total


class _Result:
    def __init__(self, output, usage=None):
        self.output = output
        self._usage = usage

    def usage(self):
        if self._usage is None:
            raise AttributeError("no usage")
        return self._usage


class _Agent:
    def __init__(self, output, usage=None):
        self._output = output
        self._usage = usage

    def run_sync(self, prompt):
        assert "PAGE TEXT" in prompt  # the built prompt is passed through
        return _Result(self._output, self._usage)


def test_to_event_dict_normalises():
    d = _to_event_dict(SAMPLE, "https://example.jp/1")
    assert d["source_url"] == "https://example.jp/1"
    assert d["kind"] == "concert"
    assert d["performances"][0]["date"] == "2026-09-07"  # time stripped
    assert "src:" in d["rounds"][0]["notes"]  # source_quote folded in
    assert "source_quote" not in d["rounds"][0]


def test_to_event_dict_drops_dateless_rounds():
    ev = ExtractedEvent(
        name="Anisama",
        performances=[
            ExtractedPerformance(
                date="2026-07-10",
                rounds=[
                    ExtractedRound(name="先行抽選", apply_deadline="2026-06-20T23:59:00"),
                    ExtractedRound(name="アニサマ×ぴあ先行抽選予約"),  # no dates -> dropped
                ],
            )
        ],
        rounds=[ExtractedRound(name="event-wide no date")],  # no dates -> dropped
    )
    d = _to_event_dict(ev, "https://w.pia.jp/t/anisama26/")
    assert [r["name"] for r in d["performances"][0]["rounds"]] == ["先行抽選"]
    assert d.get("rounds", []) == []  # dateless event-wide round dropped


def test_extract_event_roundtrips_through_schema():
    data = extract_event("page text", "https://example.jp/1", "Title", agent=_Agent(SAMPLE))
    raw = yaml.safe_load(to_event_yaml(data))
    raw["id"] = "2026-test-llm"
    ev = Event.model_validate(raw)  # LLM draft is valid in our schema
    assert ev.artist == "TestBand" and ev.source_url.endswith("/1")
    assert ev.all_rounds[0].apply_deadline.isoformat() == "2026-06-21T23:59:00+09:00"
    assert ev.all_rounds[0].notes and "src:" in ev.all_rounds[0].notes


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


def test_usage_str_reads_token_counts():
    assert _usage_str(_Result(SAMPLE, _Usage(7820, 412, 8232))) == "in=7820, out=412, total=8232"
    # missing usage() -> empty string, never raises
    assert _usage_str(_Result(SAMPLE)) == ""


def test_extract_event_logs_token_usage(caplog):
    with caplog.at_level(logging.INFO, logger="scrape.llm"):
        extract_event("page text", "https://x/1", "T", agent=_Agent(SAMPLE, _Usage(100, 20, 120)))
    assert any("in=100, out=20, total=120" in r.message for r in caplog.records)


class _ListAgent:
    """Fake translate agent: returns a canned list[str] for any prompt."""

    def __init__(self, output):
        self._output = output

    def run_sync(self, prompt):
        return _Result(self._output)


class _BoomAgent:
    def run_sync(self, prompt):
        raise AssertionError("agent should not be called when nothing needs translating")


def test_translate_event_fills_missing_name_en_and_dedupes():
    data = {
        "name": "虹ヶ咲 8thライブ",
        "performances": [
            {"date": "2026-06-06", "rounds": [{"name": "一般発売（先着）"}]},
            {"date": "2026-06-07", "rounds": [{"name": "一般発売（先着）"}]},  # same name -> dedupe
        ],
        "rounds": [{"name": "FC先行", "name_en": "FC presale"}],  # already translated -> untouched
    }
    # Two unique untranslated strings, in first-seen order: the title, then 一般発売（先着）.
    agent = _ListAgent(["Nijigasaki 8th Live", "General sale (first-come)"])
    out = translate_event(data, agent=agent)
    assert out["name_en"] == "Nijigasaki 8th Live"
    assert out["performances"][0]["rounds"][0]["name_en"] == "General sale (first-come)"
    assert out["performances"][1]["rounds"][0]["name_en"] == "General sale (first-come)"
    assert out["rounds"][0]["name_en"] == "FC presale"  # left as-is


def test_translate_event_noop_when_complete():
    data = {"name": "X", "name_en": "X", "rounds": [{"name": "r", "name_en": "R"}]}
    assert translate_event(data, agent=_BoomAgent()) == data  # agent never invoked
