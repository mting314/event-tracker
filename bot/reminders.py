"""Pure reminder logic — no Discord, no DB, fully unit-testable.

Given the event list, a user's subscriptions/settings and the current time, work
out which reminders are due. The caller (``main.py``) handles dedup via the DB
and actually sends the messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# 3 days, 1 day, 2 hours before each date.
DEFAULT_LEAD_SECONDS = [3 * 86400, 86400, 2 * 3600]

# date_type -> bilingual label for the message
DATE_LABELS = {
    "apply_open": "opens 申込開始",
    "apply_deadline": "deadline 申込締切",
    "results_date": "results 結果発表",
    "payment_deadline": "payment 入金締切",
}


def occ_key(event_id: str, round_name: str, date_type: str, lead: int, leg: str = "") -> str:
    return f"{event_id}|{round_name}|{leg}|{date_type}|{lead}"


@dataclass
class DueReminder:
    user_id: str
    event_id: str
    event_name: str
    round_name: str
    date_type: str
    target: datetime
    lead: int
    occ_key: str
    # Other leads for the same occurrence that are already past — mark them sent
    # without sending, so a late subscriber gets one message, not a burst.
    suppress_keys: list[str] = field(default_factory=list)


def _matches(sub: dict, event: dict) -> bool:
    if sub["kind"] == "event":
        return sub["target"] == event["id"]
    if sub["kind"] == "series":
        return sub["target"] in (event.get("series") or [])
    return False


def occurrences(events: list[dict]):
    """Yield every dated round action across all events."""
    for ev in events:
        for rnd in ev.get("rounds", []):
            for dtype in DATE_LABELS:
                iso = rnd.get(dtype)
                if not iso:
                    continue
                yield ev, rnd, dtype, datetime.fromisoformat(iso)


def humanize(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def due_for_user(events, subs, lead_times, now, was_sent) -> list[DueReminder]:
    """Reminders due *now* for one user.

    For each occurrence the user is subscribed to, find the leads whose window has
    opened (target-lead <= now < target) and that haven't been sent. Send the one
    nearest the date; suppress the rest so we never spam on a late subscribe.
    """
    out: list[DueReminder] = []
    if not subs:
        return out
    for ev, rnd, dtype, target in occurrences(events):
        if not any(_matches(s, ev) for s in subs):
            continue
        if now >= target:
            continue  # the date itself has passed
        leg = rnd.get("leg") or ""
        due = []
        for lead in lead_times:
            if target.timestamp() - lead <= now.timestamp():
                k = occ_key(ev["id"], rnd["name"], dtype, lead, leg)
                if not was_sent(k):
                    due.append((lead, k))
        if not due:
            continue
        due.sort()  # smallest lead first = nearest the date
        chosen_lead, chosen_key = due[0]
        out.append(
            DueReminder(
                user_id="",  # filled by caller
                event_id=ev["id"],
                event_name=ev["name"],
                round_name=rnd["name"] + (f" · {leg}" if leg else ""),
                date_type=dtype,
                target=target,
                lead=chosen_lead,
                occ_key=chosen_key,
                suppress_keys=[k for _, k in due[1:]],
            )
        )
    return out


def format_reminder(r: DueReminder) -> str:
    label = DATE_LABELS.get(r.date_type, r.date_type)
    when = r.target.strftime("%Y-%m-%d %H:%M JST")
    return f"⏰ **{r.event_name}** — {r.round_name}\n{label} in ~{humanize(r.lead)} · {when}"


def new_events_for_user(events, subs, was_notified) -> list[dict]:
    """Events matching a *series* subscription the user hasn't been told about."""
    fresh = []
    series_subs = [s for s in subs if s["kind"] == "series"]
    if not series_subs:
        return fresh
    for ev in events:
        if any(_matches(s, ev) for s in series_subs) and not was_notified(ev["id"]):
            fresh.append(ev)
    return fresh
