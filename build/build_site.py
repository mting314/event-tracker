"""Build the static site and the compiled ``events.json``.

Pipeline:
  events/*.yaml  --validate-->  data/events.json  --render-->  site/dist/

The same ``data/events.json`` is published to GitHub Pages (consumed by the
site's client JS) and fetched by the Discord bot. Run with::

    python -m build.build_site
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from schema.models import JST, KINDS, SERIES, load_all_events

ROOT = Path(__file__).resolve().parent.parent
EVENTS_DIR = ROOT / "events"
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "build" / "templates"
STATIC_DIR = ROOT / "build" / "static"
DIST_DIR = ROOT / "site" / "dist"

# date_type -> (bilingual label, short css class)
# field -> bilingual label + css class. The site localizes ja/en client-side.
DATE_TYPES = {
    "apply_open": {"ja": "申込開始", "en": "Opens", "css": "opens"},
    "apply_deadline": {"ja": "申込締切", "en": "Deadline", "css": "deadline"},
    "results_date": {"ja": "結果発表", "en": "Results", "css": "results"},
    "payment_deadline": {"ja": "入金締切", "en": "Payment", "css": "payment"},
}


def _round_occurrences(rnd) -> list[dict]:
    """The dated actions (opens/deadline/results/payment) for one round."""
    leg = f" · {rnd.leg}" if rnd.leg else ""
    round_en = (rnd.name_en or rnd.name) + leg
    out = []
    for field, meta in DATE_TYPES.items():
        dt = getattr(rnd, field)
        if dt is None:
            continue
        out.append(
            {
                "iso": dt.isoformat(),
                "label_ja": meta["ja"],
                "label_en": meta["en"],
                "css": meta["css"],
                "round": rnd.name + leg,
                "round_en": round_en,
                "apply_url": rnd.apply_url,
            }
        )
    return out


def build_index_groups(events) -> list[dict]:
    """Per-event groups for the Upcoming page: each performance carries its own
    dated round occurrences (deadlines shown under their show), plus a flat
    ``occurrences`` list so the client can pick the event's *next* deadline.
    """
    groups = []
    for ev in events:
        perfs, all_occ = [], []
        for p in ev.performances:
            occ = []
            for rnd in p.rounds:
                occ.extend(_round_occurrences(rnd))
            occ.sort(key=lambda o: o["iso"])
            all_occ.extend(occ)
            perfs.append(
                {
                    "date": p.date.isoformat(),
                    "city": p.city,
                    "venue": p.venue,
                    "label": p.label,
                    "doors": p.doors,
                    "starts": p.starts,
                    "occurrences": occ,
                }
            )
        all_occ.sort(key=lambda o: o["iso"])
        groups.append(
            {
                "id": ev.id,
                "name": ev.name,
                "name_en": ev.name_en or ev.name,
                "artist": ev.artist,
                "series": ev.series,
                "kind": ev.kind,
                "franchise": ev.franchise,
                "venues": ev.venues,
                "performers": ev.performers,
                # apply deadlines (ISO) — drives the index "has open round" filter
                "deadlines": [
                    r.apply_deadline.isoformat() for r in ev.all_rounds if r.apply_deadline
                ],
                "performances": perfs,
                "occurrences": all_occ,
            }
        )
    return groups


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
    env.filters["series_ja"] = lambda s: SERIES.get(s, s)  # canonical EN -> JP display

    groups = build_index_groups(events)
    # Events with deadline-bearing lottery rounds, for the index "Open now"
    # section (grouped by event). Whether a round is *currently* open depends on
    # view time, so the client filters by apply_open/apply_deadline and hides
    # events/rounds that aren't active. Rounds are deduped (a leg-wide round
    # repeated under each show collapses to one).
    lotteries = []
    for ev in events:
        rounds, seen = [], set()
        for r in ev.all_rounds:
            if not r.apply_deadline:
                continue
            key = (r.name, r.apply_deadline.isoformat())
            if key in seen:
                continue
            seen.add(key)
            rounds.append(
                {
                    "round": r.name,
                    "round_en": r.name_en or r.name,
                    "apply_open": r.apply_open.isoformat() if r.apply_open else "",
                    "apply_deadline": r.apply_deadline.isoformat(),
                    "apply_url": r.apply_url or "",
                }
            )
        if rounds:
            lotteries.append(
                {
                    "id": ev.id,
                    "name": ev.name,
                    "name_en": ev.name_en or ev.name,
                    "franchise": ev.franchise,
                    "rounds": rounds,
                }
            )
    # The edit API URL (public Cloud Function) is baked into the add page so the
    # editor only ever enters the admin secret. Override via EDIT_API_URL.
    edit_api = os.environ.get("EDIT_API_URL", "https://ll-commit-g6hnlr7cca-uc.a.run.app")
    # Series dropdown options for the add/edit form: the canonical SERIES vocabulary
    # plus any other tag already used in events/ (so curated tags stay suggestible).
    series_options = sorted(set(SERIES) | {s for e in events for s in e.series})
    # Kind dropdown options: the controlled vocabulary (schema.KINDS). The Event.kind
    # validator coerces any unknown kind to 'other', so KINDS is exhaustive here.
    kind_options = list(KINDS)
    # `base` is the relative path back to dist root, so links work at any depth.
    common = {
        "date_types": DATE_TYPES,
        "event_count": len(events),
        "base": "",
        "edit_api": edit_api,
        "series_options": series_options,
        "kind_options": kind_options,
    }

    env.get_template("index.html").stream(groups=groups, lotteries=lotteries, **common).dump(
        str(DIST_DIR / "index.html")
    )
    env.get_template("calendar.html").stream(**common).dump(str(DIST_DIR / "calendar.html"))
    env.get_template("add.html").stream(**common).dump(str(DIST_DIR / "add.html"))

    # Past-events archive (separate, generated by `python -m scrape.backfill`).
    past_path = DATA_DIR / "past_events.json"
    past = json.loads(past_path.read_text(encoding="utf-8"))["events"] if past_path.exists() else []
    env.get_template("past.html").stream(past=past, **common).dump(str(DIST_DIR / "past.html"))

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
