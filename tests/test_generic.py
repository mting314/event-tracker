"""Offline test for the generic 【label】 FC-page parser."""

from datetime import date, datetime

from scrape.generic import parse_generic

FIXTURE = """
<html><head>
<meta property="og:title" content="MyBand 9月公演 会員限定チケット先行受付開始！｜MyBand｜公式FC">
</head><body>
<p>公演概要①</p>
<p>【タイトル】</p><p>MyBand「Show A」</p>
<p>【公演日程】</p><p>2026年9月7日(月)</p><p>開場 18:15 / 開演 19:00</p>
<p>【会場】</p><p>東京・下北沢シャングリラ</p><p>〒155-0031 東京都世田谷区北沢2丁目4-5</p>
<p>【チケット料金】</p><p>スタンディング：￥8,200(税込)</p>
<p>--------------------</p>
<p>■チケット先行受付期間 ※抽選※</p>
<p>2026年6月5日(金)12:00～6月21日(日)23:59</p>
</body></html>
"""


def test_generic_parses_perf_and_round():
    d = parse_generic(FIXTURE, "https://example.jp/news/1")
    assert d["artist"] == "MyBand" and d["kind"] == "concert"
    assert d["source_url"].endswith("/news/1")
    assert len(d["performances"]) == 1
    p = d["performances"][0]
    assert p["date"] == date(2026, 9, 7)
    assert (
        p["venue"] == "東京・下北沢シャングリラ"
        and p["doors"] == "18:15"
        and p["starts"] == "19:00"
    )
    assert "東京都" in p["venue_address"]
    assert len(d["rounds"]) == 1
    r = d["rounds"][0]
    assert r["apply_open"] == datetime(2026, 6, 5, 12, 0)
    assert r["apply_deadline"] == datetime(2026, 6, 21, 23, 59)  # half-width (金) + year carry
