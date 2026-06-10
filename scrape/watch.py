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
from pathlib import Path

import yaml

from schema.models import JST, load_all_events

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


def round_key(deadline, name, leg) -> str:
    """Identity for diffing. The apply deadline is the stable cross-source key
    (round *names* and *legs* differ between official JP pages and our data, but
    the deadline is the same fact). Fall back to name+leg when there's no deadline.
    """
    d = _wall(deadline)
    return f"dl:{d}" if d else f"nm:{name or ''}|{leg or ''}"


def diff_rounds(parsed: list[dict], existing_rounds: list) -> dict:
    """Return {'new': [...]} — parsed (official) rounds we don't yet track.

    Keyed by apply deadline so a renamed/re-legged round isn't a false positive;
    a genuinely new round (or a moved deadline) surfaces as `new` for review.
    """
    existing = {round_key(r.apply_deadline, r.name, r.leg) for r in existing_rounds}
    new = [
        r
        for r in parsed
        if round_key(r.get("apply_deadline"), r.get("name"), r.get("leg")) not in existing
    ]
    return {"new": new}


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
                f"🆕 NEW EVENT (no tracked id={eid}): {parsed['name']} — {len(parsed['rounds'])} rounds"
            )
            drafts.append((eid or "new-event", parsed["rounds"]))
            continue

        d = diff_rounds(parsed["rounds"], ev.rounds)
        n = len(d["new"])
        total_new += n
        print(f"• {eid}: {(str(n) + ' new/updated rounds') if n else 'up to date'}")
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

    print(f"\nSummary: {total_new} new/updated rounds, {total_unknown} new events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
