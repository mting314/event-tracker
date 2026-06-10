"""Parser for official lovelive-anime.jp live pages.

These pages are the authoritative, kept-current source. They use a very
consistent layout that maps cleanly onto our model:

    神奈川公演                         <- leg heading
    （終了）オフィシャル先行抽選         <- round name (（終了） = ended)
    受付URL：https://eplus.jp/...      <- apply_url
    【申込受付期間】2026年1月10日…12:00～1月18日…23:59   <- apply_open ~ apply_deadline
    【抽選結果発表・入金期間】… ～ …21:00                <- results_date ~ payment_deadline

Parsing is a pure function over HTML so it can be tested offline.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .util import parse_jp_range

# The site 403s a bare requests UA; it wants a real browser UA + ja Accept-Language.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

_ROUND_KW = ("抽選", "先行", "発売", "当日券", "一般")
_LEG_RE = re.compile(r"^(.{1,10}?)公演$")
_ANGLE_RE = re.compile(r"[＜<](.+?)[＞>]")
# leg/city embedded in a target line, e.g. "＜Bloom Stage／福岡公演＞" -> 福岡
_TARGET_LEG_RE = re.compile(r"[／/＜<：:、]([^／/＜＞<>：:、]{1,12}?)公演")


def _strip_ended(name: str) -> tuple[str, bool]:
    ended = "終了" in name
    return re.sub(r"[（(]\s*終了\s*[）)]", "", name).strip(), ended


_SUBROUND_RE = re.compile(r"^【(.+?)】$")


def _is_bare_round_heading(line: str) -> bool:
    """A short line naming a round.

    Real round names ('ラブライブ！先行', '（終了）オフィシャル2次抽選', '一般発売（先着）')
    never contain a colon; sublabels do ('商品発売日：', '受付URL：'), so a colon
    disqualifies. '【1次抽選】'-style sub-rounds are allowed (bracket stripped).
    """
    s = line.strip()
    sub = _SUBROUND_RE.match(s)
    core = sub.group(1) if sub else s
    if "＜" in s or "公演" in s or "発売日" in core or "発売中" in core:
        return False
    if not sub:
        if s.startswith(("※", "【", "■", "★", "受付URL", "・", "http")):
            return False
        if "：" in s or ":" in s:
            return False
    if len(core) > 40:
        return False
    return any(k in core for k in _ROUND_KW)


def _round_type(name: str) -> str:
    n = name
    if "CLUB" in n or "ファンクラブ" in n or "FC" in n:
        return "fanclub"
    if "CD" in n or "ファンディスク" in n or "シングル" in n:
        return "cd-lottery"
    if "当日券" in n or "一般" in n or "先着" in n or "発売" in n:
        return "general"
    if "先行" in n or "抽選" in n:
        return "presale"
    return "other"


def parse_official(html: str, url: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    name = re.split(r"\s*[|｜]\s*", title)[0].strip() or "TODO event name"

    lines = [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]
    rounds = parse_rounds(lines)
    return {"name": name, "official_url": url, "rounds": rounds}


_DATE_FIELDS = ("apply_open", "apply_deadline", "results_date", "payment_deadline")


def _value(line: str) -> str:
    """Strip a marker label like '■受付期間：' or '【申込受付期間】' off the front."""
    return re.split(r"[：】]", line, maxsplit=1)[-1]


def _fill_marker(rnd: dict, line: str) -> None:
    if "受付URL" in line:
        cand = _value(line).strip()
        if cand.startswith("http"):
            rnd["apply_url"] = cand
    elif "入金期間" in line or ("結果発表" in line and "入金" in line):
        s, e = parse_jp_range(_value(line))
        if s and not rnd.get("results_date"):
            rnd["results_date"] = s
        if e:
            rnd["payment_deadline"] = e
    elif "当落発表" in line or "結果発表" in line:
        s, _ = parse_jp_range(_value(line))
        if s:
            rnd["results_date"] = s
    elif "受付期間" in line:  # matches both 受付期間 and 申込受付期間
        s, e = parse_jp_range(_value(line))
        if s:
            rnd["apply_open"] = s
        if e:
            rnd["apply_deadline"] = e


def parse_rounds(lines: list[str]) -> list[dict]:
    """Handle both official layouts:

    yuigaoka: 'XX公演' section heading, bare round-name line, then 【…】 markers.
    newer:    '＜RoundName＞', per-leg '★申込対象：＜…／City公演＞', then ■… markers.
    """
    rounds: list[dict] = []
    cur = None
    pending_name = None  # from a ＜…＞ heading, applied at the next 申込対象 block
    section_leg = None  # from a bare 'XX公演' heading (yuigaoka)

    def flush():
        nonlocal cur
        if cur and any(cur.get(k) for k in _DATE_FIELDS):
            rounds.append(cur)
        cur = None

    def start(name, leg):
        nonlocal cur
        flush()
        nm, ended = _strip_ended(re.sub(r"^【(.+?)】$", r"\1", (name or "抽選").strip()))
        cur = {"name": nm, "type": _round_type(nm), "leg": leg, "ended": ended}

    for line in lines:
        ang = _ANGLE_RE.search(line)
        # ＜RoundName＞ (newer): a bracketed name with a round keyword and no 公演
        if ang and "公演" not in ang.group(1) and any(k in ang.group(1) for k in _ROUND_KW):
            flush()
            pending_name, _ = _strip_ended(ang.group(1))
            continue
        # per-leg target line (newer) -> begins a round instance
        if "申込対象" in line or "対象公演" in line:
            m = _TARGET_LEG_RE.search(line)
            start(pending_name, m.group(1) if m else None)
            continue
        # bare 'XX公演' section heading (yuigaoka)
        leg_m = _LEG_RE.match(line)
        if leg_m and "＜" not in line:
            section_leg = leg_m.group(1)
            continue
        # bare round-name heading (yuigaoka)
        if _is_bare_round_heading(line):
            start(line, section_leg)
            continue
        # marker lines feed the current round (start one if a ＜…＞ name is pending)
        if any(t in line for t in ("受付URL", "受付期間", "当落発表", "結果発表", "入金期間")):
            if cur is None:
                start(pending_name, section_leg)
            _fill_marker(cur, line)

    flush()
    return rounds


def fetch(url: str) -> str:
    import requests

    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def scrape(url: str) -> dict:
    return parse_official(fetch(url), url)
