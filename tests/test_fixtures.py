"""Golden-snapshot regression tests for the extractors, against *real* saved pages.

Each fixture pairs a recorded input (real fetched HTML / X JSON payloads / an
ll-fans API response) with a blessed golden of the parsed output. The tests run
fully **offline** — they parse the saved input and assert it still equals the
golden, so any change to an extractor that alters output is caught immediately.

Adding / refreshing fixtures (needs network — run locally, then commit):

    python tests/test_fixtures.py record            # fetch every fixture's live
                                                    # page, save it, re-bless goldens
    python tests/test_fixtures.py record <name> …   # only the named fixtures
    python tests/test_fixtures.py bless             # offline: re-parse saved
                                                    # inputs and rewrite goldens
                                                    # (use after an intentional
                                                    # extractor change — review the
                                                    # diff before committing)

`record` touches the network; `bless` does not. CI only ever runs the tests.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from scrape import eventernote, generic, ingest, llfans, official, x_post
from scrape.util import to_event_yaml

FIX = Path(__file__).parent / "fixtures"
PAGES = FIX / "pages"
EXPECTED = FIX / "expected"

# Fixture registry. `kind` selects how to fetch (record) and parse (test):
#   html   — a real page parsed by `adapter`'s parse function
#   llfans — a saved ll-fans GraphQL tour, parsed by llfans.from_tour
#   x      — saved X embed+GraphQL payloads + a nested page, run through ingest_url
FIXTURES = [
    {
        "name": "official_nijigasaki_8th",
        "kind": "html",
        "adapter": "official",
        "url": "https://lovelive-anime.jp/nijigasaki/live/live_detail.php?p=8thlive#ticket",
    },
    {
        # Single-venue 体育祭 page: flat ■日程／■会場 layout, no ＜City公演＞ blocks.
        # The golden encodes the day-line → performance fallback so a regression
        # (rounds parsed but 0 performances → YAML render crash) fails the build.
        "name": "official_yuigaoka_taiikusai",
        "kind": "html",
        "adapter": "official",
        "url": "https://www.lovelive-anime.jp/yuigaoka/live/live_detail.php?p=taiikusai",
    },
    {
        "name": "generic_lustqueen",
        "kind": "html",
        "adapter": "generic",
        "url": "https://lustqueen.info/news/detail/81252",
    },
    {
        "name": "eventernote_llfest15th",
        "kind": "html",
        "adapter": "eventernote",
        "url": "https://www.eventernote.com/events/464371",
    },
    {
        "name": "llfans_aqours_1",
        "kind": "llfans",
        "tour_id": "1",
        "url": "https://ll-fans.jp/data/event/1",
    },
    {
        # The note-tweet → follow-the-link integration that kept regressing.
        "name": "x_nijigaku_8th",
        "kind": "x",
        "url": "https://x.com/Nijigaku_movie/status/2060209517850480726",
        "nested": "official_nijigasaki_8th",  # the page the post links to
    },
]

_PARSERS = {
    "official": official.parse_official,
    "generic": generic.parse_generic,
    "eventernote": eventernote.parse_eventernote,
}


def _jsonify(obj):
    """Round-trip a parsed result to plain JSON types (dates -> ISO) for comparison."""
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def _emulate_x_fetch(synd: dict | None, gql: dict | None):
    """Mirror x_post.fetch's payload→(text, links, year) logic, offline."""
    text, links, year = "", [], None
    if synd:
        text, links, year = x_post._from_payload(synd)
    if not any(x_post._followable(link) for link in links) and gql:
        g_text, g_links, g_year = x_post._from_payload(gql)
        if g_links or len(g_text) > len(text):
            text = g_text or text
            links = list(dict.fromkeys(links + g_links))
            year = year or g_year
    return text, links, year


def compute(spec: dict) -> dict:
    """Parse a fixture's saved input(s) into the result we snapshot. Offline."""
    name = spec["name"]
    if spec["kind"] == "html":
        html = (PAGES / f"{name}.html").read_text(encoding="utf-8")
        return _PARSERS[spec["adapter"]](html, spec["url"])
    if spec["kind"] == "llfans":
        tour = json.loads((PAGES / f"{name}.tour.json").read_text(encoding="utf-8"))
        return llfans.from_tour(tour, spec["url"])
    if spec["kind"] == "x":
        synd_path = PAGES / f"{name}.syndication.json"
        gql_path = PAGES / f"{name}.graphql.json"
        synd = json.loads(synd_path.read_text(encoding="utf-8")) if synd_path.exists() else None
        gql = json.loads(gql_path.read_text(encoding="utf-8")) if gql_path.exists() else None
        text, links, year = _emulate_x_fetch(synd, gql)
        nested_html = (PAGES / f"{spec['nested']}.html").read_text(encoding="utf-8")
        with (
            patch("scrape.x_post.fetch", return_value=(text, links, year)),
            patch("scrape.official.fetch", return_value=nested_html),
            patch("scrape.llm.scrape", side_effect=AssertionError("LLM must not run here")),
        ):
            res = ingest.ingest_url(spec["url"], allow_llm=False)
        # Snapshot the routing + the rendered YAML (what actually lands in events/).
        return {"adapter": res.adapter, "yaml": to_event_yaml(res.data)}
    raise ValueError(f"unknown fixture kind: {spec['kind']}")


@pytest.mark.parametrize("spec", FIXTURES, ids=[f["name"] for f in FIXTURES])
def test_fixture_matches_golden(spec):
    golden_path = EXPECTED / f"{spec['name']}.json"
    assert golden_path.exists(), (
        f"no golden for {spec['name']} — run `python tests/test_fixtures.py record`"
    )
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    actual = _jsonify(compute(spec))
    assert actual == golden, (
        f"{spec['name']} drifted from its golden. If this change is intended, "
        f"re-bless with `python tests/test_fixtures.py bless` and review the diff."
    )


# --------------------------------------------------------------------------- #
# Recording / blessing (network for `record`; offline for `bless`). __main__   #
# only — never exercised by pytest.                                            #
# --------------------------------------------------------------------------- #
def _record_inputs(spec: dict) -> None:
    """Fetch a fixture's live source and save the raw input(s). Network."""
    name = spec["name"]
    if spec.get("synthetic"):
        print(f"  skip fetch (synthetic): {name}")
        return
    if spec["kind"] == "html":
        fetch = {
            "official": official.fetch,
            "generic": generic.fetch,
            "eventernote": eventernote.fetch,
        }
        (PAGES / f"{name}.html").write_text(fetch[spec["adapter"]](spec["url"]), encoding="utf-8")
    elif spec["kind"] == "llfans":
        tour = llfans.query_tour(spec["tour_id"])
        (PAGES / f"{name}.tour.json").write_text(
            json.dumps(tour, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif spec["kind"] == "x":
        tid = x_post._tweet_id(spec["url"])
        for suffix, getter in (
            ("syndication", x_post._syndication_payload),
            ("graphql", x_post._graphql_payload),
        ):
            try:
                payload = getter(tid)
            except Exception as exc:  # noqa: BLE001 - one channel can be unavailable
                print(f"  warn: {name}.{suffix} fetch failed ({exc})")
                continue
            (PAGES / f"{name}.{suffix}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(f"  recorded inputs: {name}")


def _bless(spec: dict) -> None:
    golden = _jsonify(compute(spec))
    (EXPECTED / f"{spec['name']}.json").write_text(
        json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"  blessed golden: {spec['name']}")


def _main(argv: list[str]) -> int:
    PAGES.mkdir(parents=True, exist_ok=True)
    EXPECTED.mkdir(parents=True, exist_ok=True)
    mode = argv[0] if argv else "record"
    names = set(argv[1:])
    specs = [s for s in FIXTURES if not names or s["name"] in names]
    if not specs:
        print(f"no fixtures match {names}")
        return 1
    if mode == "record":
        for s in specs:
            _record_inputs(s)
    elif mode != "bless":
        print(f"usage: python {Path(__file__).name} [record|bless] [name ...]")
        return 1
    for s in specs:  # bless after recording so X fixtures can read nested pages
        _bless(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
