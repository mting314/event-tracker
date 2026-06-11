"""Lightweight validation for web-submitted event YAML (no pydantic dep).

Mirrors the core schema rules so a bad direct-to-main commit is rejected up front.
(The CI build still runs the full pydantic validation as a backstop — a bad file
fails the deploy build rather than breaking the live site.)
"""

from __future__ import annotations

import re

import yaml

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE_FIELDS = ("apply_open", "apply_deadline", "results_date", "payment_deadline")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def validate_event_yaml(slug: str, text: str) -> dict:
    """Parse + sanity-check; return the data dict. Raises ValueError if invalid."""
    if not _SLUG.match(slug or ""):
        raise ValueError("slug must be lowercase letters, digits and hyphens")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("YAML must be a mapping")
    if not data.get("name"):
        raise ValueError("name is required")
    for p in data.get("performances") or []:
        d = str(p.get("date", ""))
        if not _ISO_DATE.match(d):
            raise ValueError(f"performance date {d!r} must be YYYY-MM-DD")
    for r in data.get("rounds") or []:
        if not r.get("name"):
            raise ValueError("every round needs a name")
        if not any(r.get(k) for k in _DATE_FIELDS):
            raise ValueError(f"round {r.get('name')!r} needs at least one date")
    return data
