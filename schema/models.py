"""Pydantic models for events and their lottery (klottery) rounds.

The source of truth is one YAML file per event under ``events/``. These models
validate that data and normalise all datetimes to JST (Japan Standard Time,
UTC+9), which is how Japanese organisers announce application windows.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

JST = timezone(timedelta(hours=9))

# The date fields a round can carry, in the order they happen. Used by the bot
# scheduler and the site to know what to remind about / display.
ROUND_DATE_FIELDS = ("apply_open", "apply_deadline", "results_date", "payment_deadline")

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _to_jst(value):
    """Coerce a value to a JST-aware datetime.

    Naive datetimes (and plain dates) are assumed to already be JST. Aware
    datetimes are converted into JST so serialisation is consistent (+09:00).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.strip())
    else:
        raise TypeError(f"unsupported datetime value: {value!r}")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


class Performance(BaseModel):
    """A single show within a tour: one date at one venue.

    Mirrors the-sorter's performance granularity (tourName + date + venue); a
    multi-day, multi-city tour is many performances under one Event.
    """

    model_config = ConfigDict(extra="forbid")

    date: date
    venue: str | None = None
    venue_address: str | None = None
    city: str | None = None  # leg label, e.g. "Kanagawa", "Saitama"
    label: str | None = None  # e.g. "Day 1", "Night Session"
    doors: str | None = None  # "16:00"
    starts: str | None = None  # "17:00"


class Round(BaseModel):
    """A single lottery / sale round for an event (e.g. 1次先行, 一般販売).

    ``leg`` scopes the round to part of a tour (e.g. only the Kanagawa shows),
    since lotteries are usually run per-leg. Omit for tour-wide rounds.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str | None = None  # fanclub / presale / general / ...
    leg: str | None = None  # which performances this applies to, e.g. "Kanagawa"
    apply_open: datetime | None = None
    apply_deadline: datetime | None = None
    results_date: datetime | None = None
    payment_deadline: datetime | None = None
    apply_url: str | None = None
    notes: str | None = None

    @field_validator(
        "apply_open", "apply_deadline", "results_date", "payment_deadline", mode="before"
    )
    @classmethod
    def _normalise_dt(cls, v):
        return _to_jst(v)

    @model_validator(mode="after")
    def _require_a_date(self):
        if not any(getattr(self, f) for f in ROUND_DATE_FIELDS):
            raise ValueError(
                f"round {self.name!r} has no dates; at least one of "
                f"{', '.join(ROUND_DATE_FIELDS)} is required"
            )
        return self


class Event(BaseModel):
    """A trackable event and all of its lottery rounds."""

    model_config = ConfigDict(extra="forbid")

    id: str  # url-safe slug, also the YAML filename stem
    name: str  # the tour/event name, as announced (Japanese)
    name_en: str | None = None
    artist: str | None = None  # band/group/organizer when not a tagged series
    kind: str | None = None  # concert | release | meet-greet | goods | stream | ...
    source_url: str | None = None  # where this entry was ingested from (provenance)
    series: list[str] = []  # tags: ["Liella!"], ["Aqours"], or any franchise/group
    categories: list[str] = []  # free-form tags
    performers: list[str] = []
    performances: list[Performance] = []  # the shows that make up the tour
    eventernote_url: str | None = None
    official_url: str | None = None
    llfans_id: str | None = None  # ll-fans.jp tour id — stable cross-source join key
    image: str | None = None
    notes: str | None = None
    rounds: list[Round] = []

    @property
    def event_dates(self) -> list[date]:
        """All performance dates, sorted (derived — keeps templates simple)."""
        return sorted({p.date for p in self.performances})

    @property
    def venues(self) -> list[str]:
        seen, out = set(), []
        for p in self.performances:
            if p.venue and p.venue not in seen:
                seen.add(p.venue)
                out.append(p.venue)
        return out

    @field_validator("id")
    @classmethod
    def _check_slug(cls, v):
        if not _SLUG_RE.match(v):
            raise ValueError(f"id {v!r} must be a slug: lowercase letters, digits and hyphens")
        return v

    def public_dict(self) -> dict:
        """JSON-serialisable dict for ``events.json`` (datetimes -> ISO +09:00).

        Adds the derived ``event_dates`` / ``venues`` so the site JS and bot
        don't have to recompute them from ``performances``.
        """
        d = self.model_dump(mode="json", exclude_none=True)
        d["event_dates"] = [dt.isoformat() for dt in self.event_dates]
        d["venues"] = self.venues
        return d


def load_event(path: Path) -> Event:
    """Load and validate a single event YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("id", path.stem)
    try:
        return Event.model_validate(raw)
    except Exception as exc:  # re-raise with the offending file for clear CI errors
        raise ValueError(f"{path}: {exc}") from exc


def load_all_events(events_dir: Path) -> list[Event]:
    """Load every ``*.yaml`` under ``events_dir``, sorted by id."""
    events = [load_event(p) for p in sorted(events_dir.glob("*.yaml"))]
    ids = [e.id for e in events]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate event ids: {', '.join(sorted(dupes))}")
    return sorted(events, key=lambda e: e.id)
