"""Discover upcoming Love Live events from LLFans and flag which we already track.

    python -m scrape.discover            # list upcoming events, mark tracked / NEW

LLFans (ll-fans.jp) catalogs all LL performances; this lists tours that haven't
ended yet and cross-references our events/ so you can see what's missing. It does
NOT write anything — `/add <ll-fans URL>` to seed an event's base data, then add
the official page for the lottery deadlines.
"""

from __future__ import annotations

from datetime import datetime

from schema.models import JST, load_all_events

from . import llfans
from .util import EVENTS_DIR


def _norm(s: str | None) -> str:
    return (s or "").replace(" ", "").replace("　", "").strip()


def discover(today: str, tracked) -> list[dict]:
    """Annotate upcoming LLFans tours with whether we already track them."""
    by_lf = {e.llfans_id for e in tracked if e.llfans_id}
    by_name = {_norm(e.name) for e in tracked}
    rows = []
    for t in llfans.upcoming_tours(today):
        tid = str(t["id"])
        rows.append({**t, "tracked": tid in by_lf or _norm(t["name"]) in by_name})
    return rows


def main(argv=None) -> int:
    today = datetime.now(JST).date().isoformat()
    rows = discover(today, load_all_events(EVENTS_DIR))
    new = [r for r in rows if not r["tracked"]]
    print(f"Upcoming LL events on LLFans: {len(rows)} ({len(new)} not yet tracked)\n")
    for r in rows:
        flag = "✓ tracked" if r["tracked"] else "＋ NEW    "
        series = ", ".join(llfans.SERIES.get(s, "") for s in (r.get("seriesIds") or []))
        span = r["startsOn"] + (f"→{r['endsOn']}" if r["endsOn"] != r["startsOn"] else "")
        print(f"{flag}  {span}  {(r['name'] or '')[:48]}  [{series}]")
        if not r["tracked"]:
            print(f"            /add {llfans.event_url(r['id'])}")
    print(
        f"\n{len(new)} new. /add an ll-fans URL to seed base data (name/shows/official"
        " URL), then add the official page for the lottery deadlines."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
