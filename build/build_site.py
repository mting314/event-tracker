"""Build the static site and the compiled ``events.json``.

Pipeline:
  events/*.yaml  --validate-->  data/events.json  --render-->  site/dist/

The same ``data/events.json`` is published to GitHub Pages (consumed by the
site's client JS) and fetched by the Discord bot. Run with::

    python -m build.build_site
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from schema.models import JST, load_all_events

ROOT = Path(__file__).resolve().parent.parent
EVENTS_DIR = ROOT / "events"
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "build" / "templates"
STATIC_DIR = ROOT / "build" / "static"
DIST_DIR = ROOT / "site" / "dist"

# date_type -> (bilingual label, short css class)
DATE_TYPES = {
    "apply_open": ("申込開始 · Opens", "opens"),
    "apply_deadline": ("申込締切 · Deadline", "deadline"),
    "results_date": ("結果発表 · Results", "results"),
    "payment_deadline": ("入金締切 · Payment", "payment"),
}


def build_deadline_feed(events) -> list[dict]:
    """Flatten every dated round action across all events, sorted by time.

    The site's "upcoming" view and JS countdowns work off this feed; client JS
    filters out items already in the past relative to the viewer's clock.
    """
    feed = []
    for ev in events:
        for rnd in ev.rounds:
            for field, (label, css) in DATE_TYPES.items():
                dt = getattr(rnd, field)
                if dt is None:
                    continue
                feed.append(
                    {
                        "event_id": ev.id,
                        "event_name": ev.name,
                        "series": ev.series,
                        "round_name": rnd.name + (f" · {rnd.leg}" if rnd.leg else ""),
                        "date_type": field,
                        "label": label,
                        "css": css,
                        "iso": dt.isoformat(),
                        "apply_url": rnd.apply_url,
                    }
                )
    feed.sort(key=lambda i: i["iso"])
    return feed


def jst_fmt(iso: str) -> str:
    """Format an ISO datetime string as 'YYYY-MM-DD HH:MM JST'."""
    if not iso:
        return ""
    dt = datetime.fromisoformat(iso).astimezone(JST)
    return dt.strftime("%Y-%m-%d %H:%M JST")


def jst_date(iso: str) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone(JST).strftime("%Y-%m-%d")


def main() -> None:
    events = load_all_events(EVENTS_DIR)
    payload = {
        "generated_at": datetime.now(JST).isoformat(),
        "events": [e.public_dict() for e in events],
    }

    # 1. Write the compiled data artifact (repo copy + published copy).
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "events.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2. Fresh dist.
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)
    (DIST_DIR / "data").mkdir()
    (DIST_DIR / "data" / "events.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    shutil.copytree(STATIC_DIR, DIST_DIR / "static")

    # 3. Render pages.
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["jst_fmt"] = jst_fmt
    env.filters["jst_date"] = jst_date

    feed = build_deadline_feed(events)
    # `base` is the relative path back to dist root, so links work at any depth.
    common = {"date_types": DATE_TYPES, "event_count": len(events), "base": ""}

    env.get_template("index.html").stream(feed=feed, **common).dump(str(DIST_DIR / "index.html"))
    env.get_template("catalog.html").stream(
        events=[e.public_dict() for e in events], **common
    ).dump(str(DIST_DIR / "catalog.html"))
    env.get_template("calendar.html").stream(**common).dump(str(DIST_DIR / "calendar.html"))
    env.get_template("add.html").stream(**common).dump(str(DIST_DIR / "add.html"))

    event_dir = DIST_DIR / "event"
    event_dir.mkdir()
    detail = env.get_template("event_detail.html")
    for ev in events:
        detail.stream(event=ev, date_types=DATE_TYPES, event_count=len(events), base="../").dump(
            str(event_dir / f"{ev.id}.html")
        )

    print(f"Built {len(events)} events -> {DIST_DIR}")


if __name__ == "__main__":
    main()
