"""Offline tests for the scrape parsers and date helpers."""

from datetime import date, datetime

from scrape.eventernote import parse_eventernote
from scrape.util import find_all_dates, parse_date, parse_datetime, slugify, to_event_yaml
from scrape.x_post import _from_payload, extract_links, parse_text

EVENTERNOTE_FIXTURE = """
<html><head>
  <meta property="og:title" content="Liella! 6th LoveLive! Tour 東京公演">
  <title>eventernote</title>
</head><body>
  <h1>Liella! 6th LoveLive! Tour 東京公演</h1>
  <div class="date"><time datetime="2026-09-12">2026年9月12日(土)</time> 開演 18:00</div>
  <p>会場： <a href="/places/123">東京ガーデンシアター</a></p>
  <ul class="actors">
    <li><a href="/actors/1">伊達さゆり</a></li>
    <li><a href="/actors/2">岬なこ</a></li>
    <li><a href="/actors/1">伊達さゆり</a></li>
  </ul>
</body></html>
"""


def test_parse_date_forms():
    assert parse_date("2026年9月12日") == date(2026, 9, 12)
    assert parse_date("2026/9/12") == date(2026, 9, 12)
    assert parse_date("2026-09-12") == date(2026, 9, 12)
    assert parse_date("no date here") is None


def test_parse_datetime_with_time():
    assert parse_datetime("申込締切 2026年1月10日 23:59") == datetime(2026, 1, 10, 23, 59)
    assert parse_datetime("2026年9月12日(土) 18時") == datetime(2026, 9, 12, 18, 0)


def test_find_all_dates_dedupes_and_orders():
    dates = find_all_dates("2026/9/12 と 2026/9/13 と 2026/9/12")
    assert dates == [date(2026, 9, 12), date(2026, 9, 13)]


def test_slugify_prefixes_year():
    assert slugify("Some Event", [date(2026, 9, 12)]) == "2026-some-event"
    assert slugify("ライブ", [date(2026, 9, 12)]) == "2026-event"  # no ascii -> generic


def test_parse_eventernote_fixture():
    data = parse_eventernote(EVENTERNOTE_FIXTURE, "https://www.eventernote.com/events/1")
    assert data["name"] == "Liella! 6th LoveLive! Tour 東京公演"
    assert data["venue"] == "東京ガーデンシアター"
    assert data["performers"] == ["伊達さゆり", "岬なこ"]  # deduped, ordered
    assert date(2026, 9, 12) in data["event_dates"]
    assert data["eventernote_url"].endswith("/events/1")
    assert data["rounds"] == []  # rounds are never scraped from eventernote


def test_x_post_text_extracts_rounds():
    text = "FC先行受付\n2026年6月25日 23:59まで\n一般販売 2026年8月22日 10:00"
    data = parse_text(text, "https://x.com/foo/status/1")
    deadlines = [r["apply_deadline"] for r in data["rounds"]]
    assert datetime(2026, 6, 25, 23, 59) in deadlines
    assert datetime(2026, 8, 22, 10, 0) in deadlines


def test_extract_links_filters_x_and_media():
    text = (
        "詳細はこちら https://www.lovelive-anime.jp/event/1 \n"
        "RT https://twitter.com/foo/status/2 pic https://pic.twitter.com/abc "
        "短縮 https://t.co/xyz。"
    )
    links = extract_links(text)
    assert links == ["https://www.lovelive-anime.jp/event/1"]  # X/media/shortener dropped


def test_from_payload_ignores_author_profile_links_and_date():
    # Mirrors X's GraphQL shape: the post links the ticket page; the *author's*
    # profile carries a bio link + the account's creation date. Only the post's
    # link/text/year should be picked up.
    payload = {
        "data": {
            "tweetResult": {
                "result": {
                    "core": {
                        "user_results": {
                            "result": {
                                "legacy": {
                                    "created_at": "Mon Jan 01 00:00:00 +0000 2019",
                                    "entities": {
                                        "url": {
                                            "urls": [
                                                {"expanded_url": "https://example.com/bio-link"}
                                            ]
                                        }
                                    },
                                }
                            }
                        }
                    },
                    "legacy": {
                        "full_text": "チケット情報 https://t.co/abc",
                        "created_at": "Fri May 29 04:00:00 +0000 2026",
                        "entities": {
                            "urls": [
                                {
                                    "expanded_url": "https://lovelive-anime.jp/live/live_detail.php?p=1"
                                }
                            ]
                        },
                    },
                }
            }
        }
    }
    text, links, year = _from_payload(payload)
    assert links == ["https://lovelive-anime.jp/live/live_detail.php?p=1"]  # not the bio link
    assert "チケット情報" in text
    assert year == 2026  # the tweet's year, not the account's 2019


def test_parse_text_reads_yearless_show_dates_from_post():
    # "6/13-14＠… 開催" is the show; "発売日：5/30" is a sale date and must be skipped.
    text = "🌈チケット情報🌈\n6/13-14＠京王アリーナ TOKYOにて開催🎊\n発売日：5/30(土)12:00～"
    data = parse_text(text, "https://x.com/foo/status/1", default_year=2026)
    assert data["event_dates"] == [date(2026, 6, 13), date(2026, 6, 14)]


def test_to_event_yaml_synthesises_performances_for_rounds():
    # An X-followed ticket page yields rounds + show dates but no performances;
    # to_event_yaml must build performances so the rounds have somewhere to nest.
    data = {
        "name": "Test Live",
        "event_dates": [date(2026, 6, 13)],
        "rounds": [{"name": "1次先行", "apply_deadline": datetime(2026, 5, 1, 12, 0)}],
    }
    out = to_event_yaml(data)
    assert "performances:" in out
    assert "- date: 2026-06-13" in out
    assert "name: 1次先行" in out  # round nested under the synthesised performance


def test_parse_text_surfaces_and_prioritises_links():
    data = parse_text(
        "tickets! https://eplus.jp/sf/detail/1",
        "https://x.com/foo/status/1",
        links=["https://www.lovelive-anime.jp/event/1", "https://example.com/blog"],
    )
    # known event hosts (lovelive, eplus) sort ahead of the unknown blog
    assert data["source_links"][:2] == [
        "https://www.lovelive-anime.jp/event/1",
        "https://eplus.jp/sf/detail/1",
    ]
    assert "https://example.com/blog" in data["source_links"]
