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

from schema.models import JST, KIND_LABELS, KINDS, SERIES, load_all_events

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


def _end_of_day(d) -> str:
    """A date's last instant as JST ISO — used to bound 'open'/'past' by the show day
    (an event is live through the end of its performance day, not from midnight)."""
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=JST).isoformat()


def _effective_close(rnd, perf_date) -> str:
    """When a round stops being "open" (ISO): its apply_deadline, or — for a
    deadline-less first-come sale — the end of the show day, since a sale can't be
    open after the performance. Empty when neither is known (an open-ended round
    with no associated show date)."""
    if rnd.apply_deadline:
        return rnd.apply_deadline.isoformat()
    if perf_date:
        return _end_of_day(perf_date)
    return ""


def _round_occurrences(rnd, perf_date=None) -> list[dict]:
    """The dated actions (opens/deadline/results/payment) for one round."""
    leg = f" · {rnd.leg}" if rnd.leg else ""
    round_en = (rnd.name_en or rnd.name) + leg
    # The round's application window, so the "Open now" view can tell if it's live.
    # r_close is the effective end of "open" (real deadline, or the show day for a
    # deadline-less first-come sale) so such a sale ages out instead of staying open.
    r_open = rnd.apply_open.isoformat() if rnd.apply_open else ""
    r_deadline = rnd.apply_deadline.isoformat() if rnd.apply_deadline else ""
    r_close = _effective_close(rnd, perf_date)
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
                "r_open": r_open,
                "r_deadline": r_deadline,
                "r_close": r_close,
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
        perfs, all_occ, windows = [], [], []
        for p in ev.performances:
            occ = []
            for rnd in p.rounds:
                occ.extend(_round_occurrences(rnd, p.date))
                # "open~close" for the "has open round" filter; close is the real
                # deadline or, for a deadline-less first-come sale, the show day.
                if rnd.apply_open or rnd.apply_deadline:
                    r_open = rnd.apply_open.isoformat() if rnd.apply_open else ""
                    windows.append(f"{r_open}~{_effective_close(rnd, p.date)}")
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
                # Each round's application window "open~close" (open may be empty) —
                # drives the index "has open round" filter, so a deadline-less
                # first-come sale counts as open while live but ages out after the show.
                "windows": windows,
                # Show days (end-of-day JST) — authoritative for past vs upcoming: an
                # event is past only once its last performance is over, regardless of
                # whether any lottery rounds remain.
                "shows": [_end_of_day(d) for d in ev.event_dates],
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
    # Browsers auto-request /favicon.ico at the site root, so copy it there too.
    shutil.copy(STATIC_DIR / "favicon.ico", DIST_DIR / "favicon.ico")

    # 3. Render pages.
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["jst_fmt"] = jst_fmt
    env.filters["jst_date"] = jst_date
    env.filters["series_ja"] = lambda s: SERIES.get(s, s)  # canonical EN -> JP display

    groups = build_index_groups(events)
    # The edit API URL (public Cloud Function) is baked into the add page so the
    # editor only ever enters the admin secret. Override via EDIT_API_URL.
    edit_api = os.environ.get("EDIT_API_URL", "https://ll-commit-g6hnlr7cca-uc.a.run.app")
    # Public URL of the Discord-login subscription manager (bot/web.py). Baked into the
    # nav so "My Subscriptions" links out to it. Empty -> the nav link is hidden.
    subs_url = os.environ.get("SUBS_URL", "").rstrip("/")
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
        "subs_url": subs_url,
        "series_options": series_options,
        "kind_options": kind_options,
        "kind_labels": KIND_LABELS,
    }

    env.get_template("index.html").stream(groups=groups, **common).dump(
        str(DIST_DIR / "index.html")
    )
    env.get_template("calendar.html").stream(**common).dump(str(DIST_DIR / "calendar.html"))
    env.get_template("add.html").stream(**common).dump(str(DIST_DIR / "add.html"))

    # Legal pages (Terms of Service / Privacy Policy) — needed for the Discord OAuth
    # app's required ToS/Privacy URLs. `generated_at` supplies their "Last updated" date.
    generated_at = payload["generated_at"]
    env.get_template("terms.html").stream(generated_at=generated_at, **common).dump(
        str(DIST_DIR / "terms.html")
    )
    env.get_template("privacy.html").stream(generated_at=generated_at, **common).dump(
        str(DIST_DIR / "privacy.html")
    )

    # Past-events archive (separate, generated by `python -m scrape.backfill`).
    past_path = DATA_DIR / "past_events.json"
    past = json.loads(past_path.read_text(encoding="utf-8"))["events"] if past_path.exists() else []
    env.get_template("past.html").stream(past=past, **common).dump(str(DIST_DIR / "past.html"))

    event_dir = DIST_DIR / "event"
    event_dir.mkdir()
    detail = env.get_template("event_detail.html")
    for ev in events:
        detail.stream(
            event=ev,
            date_types=DATE_TYPES,
            event_count=len(events),
            base="../",
            kind_labels=KIND_LABELS,
            subs_url=subs_url,
        ).dump(str(event_dir / f"{ev.id}.html"))

    print(f"Built {len(events)} events -> {DIST_DIR}")


if __name__ == "__main__":
    main()
