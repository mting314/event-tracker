"""Backfill an archive of PAST Love Live events from LLFans into data/past_events.json.

    python -m scrape.backfill

Pure archive: these events are over and have no lottery rounds, so they are NOT
tracked by the bot and NOT written to events/. The site renders them as a
searchable "Past" tab that links out to LLFans / official pages. Cheap — uses the
paginated tours list only (no per-tour detail queries).
"""

from __future__ import annotations

import json
from datetime import datetime

from schema.models import JST

from . import llfans
from .util import ROOT


def build_archive(today: str) -> list[dict]:
    """Past LLFans tours (ended before `today`), newest first, as light records."""
    rows = []
    for t in llfans.all_tours():
        end = t.get("endsOn") or t.get("startsOn") or ""
        if not end or end >= today:
            continue  # keep only events that have already ended
        rows.append(
            {
                "id": str(t["id"]),
                "name": t.get("name") or "",
                "starts": t.get("startsOn"),
                "ends": t.get("endsOn"),
                "series": [
                    llfans.SERIES[s] for s in (t.get("seriesIds") or []) if s in llfans.SERIES
                ],
                "official_url": t.get("url") or None,
                "llfans_url": llfans.event_url(t["id"]),
            }
        )
    rows.sort(key=lambda r: r.get("ends") or "", reverse=True)
    return rows


def main(argv=None) -> int:
    today = datetime.now(JST).date().isoformat()
    rows = build_archive(today)
    out = ROOT / "data" / "past_events.json"
    out.write_text(json.dumps({"events": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} past events -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
