"""Offline tests for the official lovelive-anime.jp page parser."""

from datetime import datetime
from types import SimpleNamespace

from scrape.official import parse_official
from scrape.util import parse_jp_range
from scrape.watch import diff_rounds

# Synthetic page mirroring the real layout (one line per <p> so get_text splits them).
FIXTURE = """
<html><head><title>テストライブ | ラブライブ！シリーズ Official Web Site</title></head>
<body>
<p>神奈川公演</p>
<p>（終了）オフィシャル先行抽選</p>
<p>受付URL：https://eplus.jp/test/</p>
<p>【申込受付期間】2026年1月10日（土）12:00～1月18日（日）23:59</p>
<p>【抽選結果発表・入金期間】2026年1月23日（金）13:00～1月26日（月）21:00</p>
<p>※枚数制限：4枚まで</p>
<p>愛知公演</p>
<p>オフィシャル2次抽選</p>
<p>受付URL：https://eplus.jp/test/</p>
<p>【申込受付期間】2026年1月31日（土）12:00～2月8日（日）23:59</p>
<p>【抽選結果発表】2026年2月13日（金）13:00～</p>
<p>一般発売（先着）</p>
<p>受付URL：</p>
</body></html>
"""


def test_parse_jp_range_carries_year_and_month():
    s, e = parse_jp_range("2026年1月10日（土）12:00～1月18日（日）23:59")
    assert s == datetime(2026, 1, 10, 12, 0)
    assert e == datetime(2026, 1, 18, 23, 59)  # year carried forward
    s2, e2 = parse_jp_range("2025年12月27日（土）12:00～2026年1月6日（火）23:59")
    assert (s2.year, e2.year) == (2025, 2026)
    s3, e3 = parse_jp_range("2026年2月13日（金）13:00～")
    assert s3 == datetime(2026, 2, 13, 13, 0) and e3 is None


def test_parse_official_extracts_legged_rounds():
    d = parse_official(FIXTURE, "https://www.lovelive-anime.jp/x/live_detail.php?p=test")
    assert d["name"] == "テストライブ"
    assert len(d["rounds"]) == 2  # the (先着) round has no dates -> dropped

    r0 = d["rounds"][0]
    assert r0["leg"] == "神奈川" and r0["name"] == "オフィシャル先行抽選"
    assert r0["ended"] is True
    assert r0["apply_open"] == datetime(2026, 1, 10, 12, 0)
    assert r0["apply_deadline"] == datetime(2026, 1, 18, 23, 59)
    assert r0["results_date"] == datetime(2026, 1, 23, 13, 0)
    assert r0["payment_deadline"] == datetime(2026, 1, 26, 21, 0)
    assert r0["apply_url"] == "https://eplus.jp/test/"

    r1 = d["rounds"][1]
    assert r1["leg"] == "愛知" and r1["ended"] is False
    assert r1["apply_deadline"] == datetime(2026, 2, 8, 23, 59)  # year carried from 2026
    assert r1["results_date"] == datetime(2026, 2, 13, 13, 0)
    assert r1.get("payment_deadline") is None  # single 結果発表 -> no payment window


# Multi-leg page: per-leg ＜City公演＞ schedule blocks + rounds scoped by 対象公演,
# with a goods-section "◆東京公演" heading that used to pollute every round's leg.
MULTILEG_FIXTURE = """
<html><head><title>8thライブ | ラブライブ！シリーズ Official Web Site</title></head>
<body>
<p>＜大阪公演＞</p>
<p>【日程】Day.1　2026年6月6日（土）16:00開場／17:00開演</p>
<p>Day.2　2026年6月7日（日）14:00開場／15:00開演</p>
<p>【会場】大阪城ホール</p>
<p>＜東京公演＞</p>
<p>【日程】Day.1　2026年6月13日（土）16:00開場／17:00開演</p>
<p>Day.2　2026年6月14日（日）14:00開場／15:00開演</p>
<p>【会場】京王アリーナ TOKYO</p>
<p>＜公演当日のグッズ受け渡しに関するご案内＞</p>
<p>◆東京公演</p>
<p>チケット情報</p>
<p>最速先行抽選</p>
<p>■対象公演：大阪公演DAY.1& DAY.2（2026年6月6日・7日＠大阪城ホール）</p>
<p>最速先行抽選申込券にて受付</p>
<p>■受付期間：2026年2月4日（水）12:00～2月23日（月・祝）23:59</p>
<p>■対象公演：東京公演DAY.1& DAY.2（2026年6月13日・14日＠京王アリーナ TOKYO）</p>
<p>最速先行抽選申込券にて受付</p>
<p>■受付期間：2026年3月25日（水）12:00～4月19日（日）23:59</p>
</body></html>
"""


def test_parse_official_multileg_performances_and_legs():
    d = parse_official(MULTILEG_FIXTURE, "https://lovelive-anime.jp/x/live_detail.php?p=8th")
    perfs = d["performances"]
    assert [(str(p["date"]), p["city"]) for p in perfs] == [
        ("2026-06-06", "大阪"),
        ("2026-06-07", "大阪"),
        ("2026-06-13", "東京"),
        ("2026-06-14", "東京"),
    ]
    osaka = perfs[0]
    assert (
        osaka["venue"] == "大阪城ホール"
        and osaka["doors"] == "16:00"
        and osaka["starts"] == "17:00"
    )
    # The two 最速先行 rounds keep their 対象公演 legs (大阪 / 東京), NOT the stray
    # "◆東京公演" goods heading that used to overwrite both.
    legs = sorted(r["leg"] for r in d["rounds"])
    assert legs == ["大阪", "東京"]


def test_diff_rounds_keys_on_deadline_not_name():
    # Official (JP) names/legs differ from what we store (EN), but the deadline matches.
    parsed = [
        {
            "name": "オフィシャル先行抽選",
            "leg": "神奈川",
            "apply_deadline": datetime(2026, 1, 18, 23, 59),
        },
        {"name": "新しい抽選", "leg": "愛知", "apply_deadline": datetime(2026, 3, 1, 23, 59)},
    ]
    existing = [
        SimpleNamespace(
            name="Lottery #4 (English)",
            leg="Kanagawa",
            apply_deadline=datetime(2026, 1, 18, 23, 59),
            results_date=None,
            payment_deadline=None,
        ),
    ]
    d = diff_rounds(parsed, existing)
    assert len(d["new"]) == 1  # the matching-deadline round is NOT flagged
    assert d["new"][0]["name"] == "新しい抽選"  # only the genuinely new one
