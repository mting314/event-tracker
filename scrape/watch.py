"""Watcher: poll watched sources, diff against tracked events, draft updates.

    python -m scrape.watch                # report only
    python -m scrape.watch --write        # also write drafts/ for new/changed rounds

Each source in sources.yaml may set ``adapter:`` to choose how it's parsed:
``auto`` (default — domain dispatch + LLM fallback), ``official``, ``generic``,
``eventernote``, or ``llm``. This lets the watcher poll any event source, not just
lovelive-anime.jp. Human-in-the-loop: it never edits events/ — it reports diffs and
writes draft round snippets you review and merge (CI opens a PR).
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import yaml

from schema.models import JST, ROUND_DATE_FIELDS, load_all_events

from . import eventernote, generic, official
from .ingest import ingest_url
from .util import EVENTS_DIR, _yaml_str

ROOT = Path(__file__).resolve().parent.parent
DRAFTS_DIR = ROOT / "drafts"

# Explicit per-source adapters (besides 'auto' and 'llm'). Stored as modules so
# `.scrape` resolves at call time (keeps it patchable / future-proof).
_ADAPTERS = {"official": official, "generic": generic, "eventernote": eventernote}


def scrape_source(url: str, adapter: str | None = None) -> dict:
    """Parse a watched source with the chosen adapter; returns data with 'rounds'.

    'auto' uses the shared domain dispatch + LLM fallback; 'llm' forces the LLM.
    """
    adapter = (adapter or "auto").lower()
    if adapter == "auto":
        return ingest_url(url).data
    if adapter == "llm":
        return ingest_url(url, force_llm=True).data
    mod = _ADAPTERS.get(adapter)
    if mod is None:
        raise ValueError(f"unknown adapter '{adapter}' (auto/official/generic/eventernote/llm)")
    return mod.scrape(url)


def event_watch_url(ev) -> str | None:
    """The page to re-scan a tracked event from: its official page, else the URL
    it was ingested from. (Eventernote is a catalog, not a lottery source.)"""
    return ev.official_url or ev.source_url


def event_is_past(ev, today) -> bool:
    """True when no performance or round date is today-or-later — nothing left to
    watch, so we skip it (keeps the daily auto-scan cost bounded)."""
    if any(d >= today for d in ev.event_dates):
        return False
    for r in ev.all_rounds:
        for f in ROUND_DATE_FIELDS:
            v = getattr(r, f)
            if v is not None and v.date() >= today:
                return False
    return True


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else (v or None)


def _wall(v) -> str:
    """Minute-precision JST wall-clock key, so naive (parsed) and JST-aware
    (stored) datetimes for the same moment compare equal."""
    if not hasattr(v, "strftime"):
        return ""
    if getattr(v, "tzinfo", None) is not None:
        v = v.astimezone(JST)
    return v.strftime("%Y-%m-%dT%H:%M")


def _flat_rounds(data: dict) -> list[dict]:
    """All rounds in an ingest dict, whether nested under performances or top-level."""
    out = list(data.get("rounds") or [])
    for p in data.get("performances") or []:
        out += list(p.get("rounds") or [])
    return out


# The application window identifies a round across sources; names/legs differ
# between official JP pages and our data, but the window is the same fact.
_WINDOW_FIELDS = ("apply_open", "apply_deadline")


def _window(get) -> dict:
    """The {field: wall-clock} window timestamps a round carries. `get` reads a
    field by name from a parsed dict (``r.get``) or a model object (``r``)."""
    src = get if callable(get) else (lambda f: getattr(get, f, None))
    return {f: w for f in _WINDOW_FIELDS if (w := _wall(src(f)))}


def diff_rounds(parsed: list[dict], existing_rounds: list) -> dict:
    """Return {'new': [...]} — parsed (official) rounds we don't yet track.

    Rounds are matched on their application *window* (apply_open + apply_deadline),
    not name/leg. A parsed round is already-tracked iff some existing round shares
    every window timestamp they *both* carry (and at least one). So:
      - a renamed/re-legged round with the same window is NOT a false positive;
      - a re-scrape that dropped the deadline still matches on apply_open alone;
      - a *moved* deadline — or a genuinely distinct round in the same slot with a
        *different* apply_open — still surfaces as new for review.
    Rounds carrying no window timestamp at all fall back to name+leg identity.
    """
    existing_windows = [_window(r) for r in existing_rounds]
    existing_nm = {f"{r.name or ''}|{r.leg or ''}" for r in existing_rounds}

    def is_known(r: dict) -> bool:
        w = _window(r.get)
        if not w:
            return f"{r.get('name') or ''}|{r.get('leg') or ''}" in existing_nm
        return any(
            (common := w.keys() & ew.keys()) and all(w[f] == ew[f] for f in common)
            for ew in existing_windows
        )

    return {"new": [r for r in parsed if not is_known(r)]}


def round_to_yaml(r: dict) -> str:
    """Serialise a parsed round to YAML (schema fields only; drops 'ended')."""
    L = [f"  - name: {_yaml_str(r['name'])}"]
    if r.get("type"):
        L.append(f"    type: {_yaml_str(r['type'])}")
    if r.get("leg"):
        L.append(f"    leg: {_yaml_str(r['leg'])}")
    for f in ("apply_open", "apply_deadline", "results_date", "payment_deadline"):
        if r.get(f):
            L.append(f"    {f}: {_iso(r[f])}")
    if r.get("apply_url"):
        L.append(f"    apply_url: {r['apply_url']}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scrape.watch")
    ap.add_argument("--sources", default=str(ROOT / "sources.yaml"))
    ap.add_argument("--write", action="store_true", help="write drafts/ for diffs")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(Path(args.sources).read_text(encoding="utf-8")) or {}
    events = {e.id: e for e in load_all_events(EVENTS_DIR)}

    # `sources:` is the new key; `official_pages:` kept for back-compat.
    sources = cfg.get("sources") or cfg.get("official_pages") or []

    total_new = total_unknown = 0
    drafts = []
    for src in sources:
        url, eid = src.get("url"), src.get("id")
        try:
            parsed = scrape_source(url, src.get("adapter"))
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"⚠️  {url}: fetch/parse failed: {exc}")
            continue

        ev = events.get(eid)
        if ev is None:
            total_unknown += 1
            print(
                f"🆕 NEW EVENT (no tracked id={eid}): {parsed['name']} — {len(_flat_rounds(parsed))} rounds"
            )
            drafts.append((eid or "new-event", _flat_rounds(parsed)))
            continue

        d = diff_rounds(_flat_rounds(parsed), ev.all_rounds)
        n = len(d["new"])
        total_new += n
        print(f"• {eid}: {(str(n) + ' new/updated rounds') if n else 'up to date'}")
        for r in d["new"]:
            print(f"    + [{r.get('leg') or '-'}] {r['name']}  dl={_iso(r.get('apply_deadline'))}")
        if n:
            drafts.append((eid, d["new"]))

    # Auto-watch: re-scan every tracked event that has a source page, isn't already
    # configured above, and isn't finished — so events added via /add are monitored
    # for new rounds without anyone editing sources.yaml.
    configured = {s.get("id") for s in sources}
    today = datetime.now(JST).date()
    total_auto = 0
    for eid, ev in events.items():
        url = event_watch_url(ev)
        if eid in configured or not url or event_is_past(ev, today):
            continue
        total_auto += 1
        try:
            parsed = scrape_source(url, "auto")
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"⚠️  {url} (auto, {eid}): fetch/parse failed: {exc}")
            continue
        d = diff_rounds(_flat_rounds(parsed), ev.all_rounds)
        n = len(d["new"])
        total_new += n
        print(f"• {eid} (auto): {(str(n) + ' new/updated rounds') if n else 'up to date'}")
        for r in d["new"]:
            print(f"    + [{r.get('leg') or '-'}] {r['name']}  dl={_iso(r.get('apply_deadline'))}")
        if n:
            drafts.append((eid, d["new"]))

    if args.write and drafts:
        DRAFTS_DIR.mkdir(exist_ok=True)
        stamp = "rounds"  # caller (CI) can rename; avoid time-based names for determinism
        for eid, rounds in drafts:
            body = "rounds:\n" + "\n".join(round_to_yaml(r) for r in rounds) + "\n"
            (DRAFTS_DIR / f"{eid}.{stamp}.yaml").write_text(body, encoding="utf-8")
        print(f"\n✍️  wrote {len(drafts)} draft(s) to {DRAFTS_DIR}/")

    print(
        f"\nSummary: {total_new} new/updated rounds, {total_unknown} new events, "
        f"{total_auto} auto-watched events scanned"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
