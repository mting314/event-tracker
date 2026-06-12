"""Offline tests for the LLFans (ll-fans.jp) adapter — no network."""

from unittest.mock import patch

from scrape import ingest, llfans

SAMPLE_TOUR = {
    "id": "264",
    "name": "蓮ノ空 5th Live Tour ～4Pair Power Spread!!!!～",
    "seriesIds": ["6"],
    "url": "https://www.lovelive-anime.jp/hasunosora/live-event/live_detail.php?p=4PPS",
    "tourType": {"name": "ライブ・ファンミ"},
    "concerts": [
        {
            "id": "380",
            "name": "Heart Stage",
            "venue": {"name": "国立代々木競技場 第一体育館"},
            "performances": [
                {
                    "id": "655",
                    "name": "Day.1",
                    "date": "2026-10-04",
                    "openTime": "15:30:00",
                    "startTime": "17:00:00",
                    "canceled": False,
                },
                {
                    "id": "656",
                    "name": "Day.2",
                    "date": "2026-10-05",
                    "openTime": "14:00:00",
                    "startTime": "15:30:00",
                    "canceled": True,
                },
            ],
        }
    ],
}


def test_tour_id_extraction():
    assert llfans.tour_id("https://ll-fans.jp/data/event/264") == "264"
    assert llfans.tour_id("https://ll-fans.jp/data/event/264/") == "264"
    assert llfans.tour_id("https://ll-fans.jp/data/event") is None


def test_from_tour_maps_base_event_no_rounds():
    d = llfans.from_tour(SAMPLE_TOUR, "https://ll-fans.jp/data/event/264")
    assert d["name"].startswith("蓮ノ空 5th")
    assert d["series"] == ["蓮ノ空女学院スクールアイドルクラブ"]
    assert d["kind"] == "concert"
    assert d["official_url"].endswith("p=4PPS")
    assert d["llfans_id"] == "264"
    assert d["rounds"] == []  # LLFans never has lottery rounds
    # canceled performance dropped; times trimmed to HH:MM
    assert len(d["performances"]) == 1
    p = d["performances"][0]
    assert p["date"] == "2026-10-04" and p["venue"].startswith("国立")
    assert p["label"] == "Day.1" and p["doors"] == "15:30" and p["starts"] == "17:00"


def test_scrape_uses_query_and_url():
    with patch.object(llfans, "query_tour", return_value=SAMPLE_TOUR) as q:
        d = llfans.scrape("https://ll-fans.jp/data/event/264")
    q.assert_called_once_with("264")
    assert d["llfans_id"] == "264"


def test_pick_scraper_routes_llfans():
    assert ingest.pick_scraper("https://ll-fans.jp/data/event/264") is llfans.scrape


def test_upcoming_tours_filters_and_sorts():
    tours = [
        {
            "id": "1",
            "name": "old",
            "startsOn": "2020-01-01",
            "endsOn": "2020-01-02",
            "seriesIds": [],
        },
        {
            "id": "2",
            "name": "later",
            "startsOn": "2030-05-01",
            "endsOn": "2030-05-02",
            "seriesIds": [],
        },
        {
            "id": "3",
            "name": "soon",
            "startsOn": "2030-01-01",
            "endsOn": "2030-01-02",
            "seriesIds": [],
        },
    ]
    with patch.object(llfans, "all_tours", return_value=tours):
        up = llfans.upcoming_tours("2026-06-11")
    assert [t["id"] for t in up] == ["3", "2"]  # past dropped, soonest-first


def test_discover_flags_tracked_vs_new():
    from types import SimpleNamespace

    from scrape import discover

    tracked = [
        SimpleNamespace(llfans_id="264", name="By id"),
        SimpleNamespace(llfans_id=None, name="蓮ノ空 5th Live"),  # match by name
    ]
    tours = [
        {
            "id": "264",
            "name": "Whatever",
            "startsOn": "2030-01-01",
            "endsOn": "2030-01-01",
            "seriesIds": [],
        },
        {
            "id": "999",
            "name": "蓮ノ空 5th Live",
            "startsOn": "2030-02-01",
            "endsOn": "2030-02-01",
            "seriesIds": [],
        },
        {
            "id": "1000",
            "name": "Brand New Tour",
            "startsOn": "2030-03-01",
            "endsOn": "2030-03-01",
            "seriesIds": [],
        },
    ]
    with patch.object(llfans, "upcoming_tours", return_value=tours):
        rows = discover.discover("2026-06-11", tracked)
    by_id = {r["id"]: r["tracked"] for r in rows}
    assert by_id["264"] is True  # matched by llfans_id
    assert by_id["999"] is True  # matched by name
    assert by_id["1000"] is False  # genuinely new


def test_backfill_archive_keeps_only_past_newest_first():
    from scrape import backfill

    tours = [
        {
            "id": "1",
            "name": "old",
            "startsOn": "2020-01-01",
            "endsOn": "2020-01-02",
            "seriesIds": ["1"],
            "url": "u1",
        },
        {
            "id": "2",
            "name": "future",
            "startsOn": "2030-05-01",
            "endsOn": "2030-05-02",
            "seriesIds": [],
            "url": None,
        },
        {
            "id": "3",
            "name": "recent past",
            "startsOn": "2026-01-01",
            "endsOn": "2026-02-01",
            "seriesIds": [],
            "url": "u3",
        },
    ]
    with patch.object(llfans, "all_tours", return_value=tours):
        rows = backfill.build_archive("2026-06-11")
    assert [r["id"] for r in rows] == ["3", "1"]  # future dropped, newest-first
    assert rows[1]["series"] == ["ラブライブ！"]
    assert rows[0]["llfans_url"].endswith("/data/event/3")


def test_ingest_routes_llfans_no_llm():
    # ll-fans is a trusted domain adapter -> deterministic, never LLM.
    with (
        patch.object(llfans, "query_tour", return_value=SAMPLE_TOUR),
        patch("scrape.llm.scrape") as llm,
    ):
        res = ingest.ingest_url("https://ll-fans.jp/data/event/264")
    assert res.adapter == "llfans" and not res.used_llm
    assert res.data["llfans_id"] == "264"
    llm.assert_not_called()
