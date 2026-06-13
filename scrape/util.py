"""Shared helpers for the scrape → draft-YAML pipeline.

Parsing is split from fetching so it can be unit-tested offline against saved
HTML fixtures (the network is not available in CI or in this dev environment).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from schema.models import nest_rounds

ROOT = Path(__file__).resolve().parent.parent
EVENTS_DIR = ROOT / "events"

# Japanese / common date forms: 2026年9月12日, 2026/9/12, 2026-09-12, 2026.9.12
_DATE_RE = re.compile(r"(\d{4})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})\s*日?")
# Optional trailing time: 18:00 / 18時00分 / 18時
_TIME_RE = re.compile(r"(\d{1,2})\s*[:時]\s*(\d{1,2})?")


def parse_date(text: str) -> date | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_datetime(text: str) -> datetime | None:
    """Parse a 'date [time]' span into a naive datetime (assumed JST downstream)."""
    d = parse_date(text)
    if not d:
        return None
    # Look for a time *after* the matched date to avoid grabbing the year.
    tail = text[_DATE_RE.search(text).end() :] if _DATE_RE.search(text) else ""
    tm = _TIME_RE.search(tail)
    hh, mm = 0, 0
    if tm:
        hh = int(tm.group(1))
        mm = int(tm.group(2) or 0)
    return datetime(d.year, d.month, d.day, hh, mm)


# A Japanese datetime token: optional year, month, day, optional (weekday), optional HH:MM.
# e.g. "2026年1月10日（土）12:00", "1月18日（日）23:59", "3月 1日"
_JP_DT = re.compile(
    r"(?:(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})日(?:[（(].{0,4}[）)])?\s*(?:(\d{1,2})[:：](\d{2}))?"
)


def parse_jp_datetime(text: str, default_year: int | None = None) -> datetime | None:
    m = _JP_DT.search(text or "")
    if not m:
        return None
    y, mo, d, hh, mm = m.groups()
    year = int(y) if y else default_year
    if not year:
        return None
    return datetime(year, int(mo), int(d), int(hh or 0), int(mm or 0))


def parse_jp_range(text: str, default_year: int | None = None):
    """Parse 'START～END' (or a single date) into (start, end|None) JST-naive datetimes.

    Year/month on END are carried forward from START when omitted, which matches
    how official pages write ranges like '1月10日…～1月18日…'.
    """
    toks = list(_JP_DT.finditer(text or ""))
    if not toks:
        return None, None

    def build(m, fallback_year):
        y, mo, d, hh, mm = m.groups()
        year = int(y) if y else fallback_year
        if not year:
            return None
        return datetime(year, int(mo), int(d), int(hh or 0), int(mm or 0))

    start = build(toks[0], default_year)
    end = build(toks[1], (start.year if start else default_year)) if len(toks) > 1 else None
    return start, end


def find_all_dates(text: str) -> list[date]:
    out, seen = [], set()
    for m in _DATE_RE.finditer(text or ""):
        y, mo, d = (int(g) for g in m.groups())
        try:
            dt = date(y, mo, d)
        except ValueError:
            continue
        if dt not in seen:
            seen.add(dt)
            out.append(dt)
    return out


def slugify(name: str, dates: list[date] | None = None) -> str:
    """Best-effort slug. Japanese names have no ASCII, so prefix with the year
    and fall back to a generic stem the user can rename."""
    ascii_part = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    year = None
    if dates:
        d0 = dates[0]
        if hasattr(d0, "year"):  # date / datetime
            year = d0.year
        else:  # ISO-ish string e.g. "2026-09-07"
            m = re.match(r"\s*(\d{4})", str(d0))
            year = int(m.group(1)) if m else None
    stem = ascii_part or "event"
    return f"{year}-{stem}" if year else stem


def _yaml_str(v: str) -> str:
    """Quote a scalar for YAML when needed (keeps Japanese readable otherwise)."""
    if v is None:
        return ""
    if re.search(r'[:#\[\]{}",&*!|>%@`]', v) or v.strip() != v:
        return '"' + v.replace('"', '\\"') + '"'
    return v


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _round_yaml_lines(r: dict, indent: str) -> list[str]:
    """YAML lines for one round, list-item indented by `indent` (e.g. '    ')."""
    out = [f"{indent}- name: {_yaml_str(r.get('name') or 'TODO')}"]
    pad = indent + "  "
    if r.get("name_en"):
        out.append(f"{pad}name_en: {_yaml_str(r['name_en'])}")
    for f in ("type", "leg"):
        if r.get(f):
            out.append(f"{pad}{f}: {_yaml_str(r[f])}")
    for f in ("apply_open", "apply_deadline", "results_date", "payment_deadline"):
        if r.get(f):
            out.append(f"{pad}{f}: {_iso(r[f])}")
    if r.get("apply_url"):
        out.append(f"{pad}apply_url: {r['apply_url']}")
    if r.get("notes"):
        out.append(f"{pad}notes: {_yaml_str(r['notes'])}")
    return out


def to_event_yaml(data: dict) -> str:
    """Serialise an ingested event dict to YAML in the current schema.

    Rounds are nested under their performance. Accepts either the nested shape
    (performances[].rounds) or the legacy flat shape (top-level rounds), which is
    distributed into performances first. Tolerant of datetimes or ISO strings.
    """
    data = nest_rounds(dict(data))  # legacy flat -> nested; don't mutate caller
    L = []
    L.append(f"name: {_yaml_str(data.get('name') or 'TODO event name')}")
    for f in ("name_en", "artist", "kind"):
        if data.get(f):
            L.append(f"{f}: {_yaml_str(data[f])}")
    if data.get("series"):
        L.append("series:")
        L += [f"  - {_yaml_str(s)}" for s in data["series"]]
    if data.get("categories"):
        L.append("categories:")
        L += [f"  - {_yaml_str(c)}" for c in data["categories"]]
    if data.get("performers"):
        L.append("performers:")
        L += [f"  - {_yaml_str(p)}" for p in data["performers"]]

    perfs = data.get("performances")
    if not perfs and data.get("event_dates"):
        perfs = [{"date": _iso(d)} for d in data["event_dates"]]
    if perfs:
        L.append("performances:")
        for p in perfs:
            L.append(f"  - date: {_iso(p['date'])}")
            for f in ("city", "label", "venue", "venue_address"):
                if p.get(f):
                    L.append(f"    {f}: {_yaml_str(p[f])}")
            for f in ("doors", "starts"):
                if p.get(f):
                    L.append(f'    {f}: "{p[f]}"')
            if p.get("rounds"):
                L.append("    rounds:")
                for r in p["rounds"]:
                    L += _round_yaml_lines(r, "      ")

    for f in ("eventernote_url", "official_url", "source_url"):
        if data.get(f):
            L.append(f"{f}: {data[f]}")
    if data.get("llfans_id"):
        L.append(f"llfans_id: {_yaml_str(str(data['llfans_id']))}")
    if data.get("notes"):
        L.append(f"notes: {_yaml_str(data['notes'])}")
    return "\n".join(L) + "\n"


def render_draft_yaml(data: dict) -> str:
    """Render a human-friendly draft YAML with TODO hints for missing fields.

    Lottery rounds are almost never present on event-listing pages, so we always
    emit a commented round template for the curator to fill in.
    """
    L = []
    L.append(f"name: {_yaml_str(data.get('name') or 'TODO event name')}")
    if data.get("name_en"):
        L.append(f"name_en: {_yaml_str(data['name_en'])}")
    L.append("series:")
    for s in data.get("series") or ["TODO"]:
        L.append(f"  - {_yaml_str(s)}")
    if data.get("categories"):
        L.append("categories:")
        for c in data["categories"]:
            L.append(f"  - {_yaml_str(c)}")
    if data.get("performers"):
        L.append("performers:")
        for p in data["performers"]:
            L.append(f"  - {_yaml_str(p)}")
    else:
        L.append("performers: []  # TODO cast")
    L.append(f"venue: {_yaml_str(data.get('venue') or 'TODO venue')}")
    L.append("event_dates:")
    for d in data.get("event_dates") or []:
        L.append(f"  - {d.isoformat()}")
    if not data.get("event_dates"):
        L.append("  # - 2026-01-01  # TODO")
    if data.get("eventernote_url"):
        L.append(f"eventernote_url: {data['eventernote_url']}")
    if data.get("official_url"):
        L.append(f"official_url: {data['official_url']}")
    if data.get("notes"):
        L.append(f"notes: {_yaml_str(data['notes'])}")

    L.append("rounds:")
    rounds = data.get("rounds") or []
    if rounds:
        for r in rounds:
            L.append(f"  - name: {_yaml_str(r.get('name') or 'TODO')}")
            for f in ("apply_open", "apply_deadline", "results_date", "payment_deadline"):
                if r.get(f):
                    val = r[f]
                    val = val.isoformat() if hasattr(val, "isoformat") else val
                    L.append(f"    {f}: {val}")
            if r.get("apply_url"):
                L.append(f"    apply_url: {r['apply_url']}")
    # Always leave a template round to fill in.
    L.append("  # --- fill in the real lottery rounds below (delete this template) ---")
    L.append("  # - name: 1次先行")
    L.append("  #   type: presale")
    L.append("  #   apply_open: 2026-01-01T12:00:00")
    L.append("  #   apply_deadline: 2026-01-10T23:59:00")
    L.append("  #   results_date: 2026-01-17T15:00:00")
    L.append("  #   payment_deadline: 2026-01-21T23:00:00")
    L.append("  #   apply_url: https://...")
    return "\n".join(L) + "\n"
