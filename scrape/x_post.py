"""Best-effort helper for X (Twitter) announcements.

X aggressively blocks unauthenticated scraping, so the reliable path is to
**paste the post text** rather than fetch a URL. This extracts date/time hints
from free text into suggested rounds for the curator to refine.
"""

from __future__ import annotations

from .util import find_all_dates, parse_datetime


def parse_text(text: str, url: str | None = None) -> dict:
    """Pull round hints out of pasted announcement text."""
    suggestions = []
    for line in (text or "").splitlines():
        dt = parse_datetime(line)
        if dt:
            suggestions.append({"name": line.strip()[:60], "apply_deadline": dt})
    return {
        "official_url": url,
        "event_dates": find_all_dates(text),
        "rounds": suggestions,
        "notes": "TODO drafted from X post text — verify every date",
    }


def scrape(url: str) -> dict:
    """Try to fetch a post; on the common block, tell the user to paste text."""
    import requests

    UA = "Mozilla/5.0 (compatible; ll-lottery-tracker/0.1)"
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - any failure -> manual fallback
        raise RuntimeError(
            f"could not fetch X post ({exc}); rerun with --text and paste the post"
        ) from exc
    return parse_text(resp.text, url)
