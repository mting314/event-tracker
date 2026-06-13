"""Load the compiled events list the bot reminds against.

Prefers the published URL (decoupled from the repo host). With no source it uses
the local ``data/events.json`` build artifact, and if that's absent (fresh
checkout — the artifact is gitignored, not committed) it compiles straight from
the ``events/*.yaml`` source of truth, so the bot works in dev without network
or a build step.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "data" / "events.json"


def load_events(source: str | None = None) -> list[dict]:
    """`source` may be an http(s) URL or a file path; None -> local artifact,
    falling back to compiling from the YAML source if the artifact is missing."""
    if source and source.startswith(("http://", "https://")):
        import requests

        resp = requests.get(source, timeout=20)
        resp.raise_for_status()
        return resp.json().get("events", [])
    if source:
        return json.loads(Path(source).read_text(encoding="utf-8")).get("events", [])
    if LOCAL.exists():
        return json.loads(LOCAL.read_text(encoding="utf-8")).get("events", [])
    # events.json is a derived artifact; recompile it from the YAML source of truth.
    from schema.models import load_all_events

    return [e.public_dict() for e in load_all_events(ROOT / "events")]


def search_events(events: list[dict], query: str, limit: int = 10) -> list[dict]:
    q = query.lower().strip()
    if not q:
        return events[:limit]
    scored = []
    for ev in events:
        hay = " ".join(
            [
                ev.get("name", ""),
                ev.get("name_en", "") or "",
                " ".join(ev.get("series", [])),
                " ".join(ev.get("venues", [])),
                " ".join(ev.get("performers", [])),
            ]
        ).lower()
        if q in hay:
            scored.append(ev)
    return scored[:limit]


def all_series(events: list[dict]) -> list[str]:
    out = set()
    for ev in events:
        out.update(ev.get("series", []))
    return sorted(out)
