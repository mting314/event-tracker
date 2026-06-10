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
