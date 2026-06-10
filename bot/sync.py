"""Load the compiled events.json the bot reminds against.

Prefers the published URL (decoupled from the repo host) and falls back to the
local ``data/events.json`` so the bot works in dev without network.
"""

from __future__ import annotations

import json
from pathlib import Path

LOCAL = Path(__file__).resolve().parent.parent / "data" / "events.json"


def load_events(source: str | None = None) -> list[dict]:
    """`source` may be an http(s) URL or a file path; None -> local artifact."""
    if source and source.startswith(("http://", "https://")):
        import requests

        resp = requests.get(source, timeout=20)
        resp.raise_for_status()
        return resp.json().get("events", [])
    path = Path(source) if source else LOCAL
    return json.loads(path.read_text(encoding="utf-8")).get("events", [])


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
