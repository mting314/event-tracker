"""Offline tests for the bot's DB and pure reminder logic (no Discord)."""

from datetime import datetime, timedelta, timezone

import pytest

from bot.db import DB
from bot.reminders import (
    DEFAULT_LEAD_SECONDS,
    due_for_user,
    new_events_for_user,
)
from bot.sync import search_events

JST = timezone(timedelta(hours=9))


def make_event(eid="ev1", series=("Liella!",), deadline="2026-09-20T23:59:00+09:00"):
    return {
        "id": eid,
        "name": "Test Live",
        "series": list(series),
        "venue": "Tokyo",
        "performers": ["伊達さゆり"],
        "rounds": [{"name": "1次先行", "apply_deadline": deadline}],
    }


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "t.db")
    yield d
    d.close()


# ---------- DB ----------


def test_subscriptions_crud(db):
    assert db.add_subscription("u1", "event", "ev1") is True
    assert db.add_subscription("u1", "event", "ev1") is False  # dupe
    assert db.list_subscriptions("u1") == [{"kind": "event", "target": "ev1"}]
    assert db.all_subscriptions() == {"u1": [{"kind": "event", "target": "ev1"}]}
    assert db.remove_subscription("u1", "event", "ev1") is True
    assert db.list_subscriptions("u1") == []


def test_settings_defaults_and_update(db):
    s = db.get_settings("u1", DEFAULT_LEAD_SECONDS)
    assert s["lead_times"] == DEFAULT_LEAD_SECONDS and s["dm_enabled"] is True
    db.set_settings("u1", lead_times=[3600], dm_enabled=False)
    s = db.get_settings("u1", DEFAULT_LEAD_SECONDS)
    assert s["lead_times"] == [3600] and s["dm_enabled"] is False


# ---------- reminder timing ----------


def test_single_lead_due_at_3_days_out(db):
    ev = make_event()
    target = datetime.fromisoformat(ev["rounds"][0]["apply_deadline"])
    subs = [{"kind": "event", "target": "ev1"}]
    now = target - timedelta(days=3, seconds=-1)  # just inside the 3d window
    due = due_for_user([ev], subs, DEFAULT_LEAD_SECONDS, now, lambda k: db.was_sent("u1", k))
    assert len(due) == 1
    assert due[0].lead == 3 * 86400
    # nothing is due a week out
    assert (
        due_for_user(
            [ev],
            subs,
            DEFAULT_LEAD_SECONDS,
            target - timedelta(days=7),
            lambda k: db.was_sent("u1", k),
        )
        == []
    )


def test_lead_progression_with_dedup(db):
    ev = make_event()
    target = datetime.fromisoformat(ev["rounds"][0]["apply_deadline"])
    subs = [{"kind": "event", "target": "ev1"}]
    seen = lambda k: db.was_sent("u1", k)

    d1 = due_for_user([ev], subs, DEFAULT_LEAD_SECONDS, target - timedelta(days=3), seen)
    assert len(d1) == 1 and d1[0].lead == 3 * 86400
    db.mark_sent("u1", d1[0].occ_key, "t")
    # same moment again -> already sent -> nothing
    assert due_for_user([ev], subs, DEFAULT_LEAD_SECONDS, target - timedelta(days=3), seen) == []
    # one day out -> the 1d lead fires
    d2 = due_for_user([ev], subs, DEFAULT_LEAD_SECONDS, target - timedelta(days=1), seen)
    assert len(d2) == 1 and d2[0].lead == 86400


def test_late_subscribe_sends_one_not_a_burst(db):
    ev = make_event()
    target = datetime.fromisoformat(ev["rounds"][0]["apply_deadline"])
    subs = [{"kind": "event", "target": "ev1"}]
    now = target - timedelta(hours=1)  # all three leads already "due"
    due = due_for_user([ev], subs, DEFAULT_LEAD_SECONDS, now, lambda k: db.was_sent("u1", k))
    assert len(due) == 1
    assert due[0].lead == 2 * 3600  # nearest the deadline
    assert len(due[0].suppress_keys) == 2  # 3d + 1d suppressed


def test_no_reminder_after_deadline(db):
    ev = make_event()
    target = datetime.fromisoformat(ev["rounds"][0]["apply_deadline"])
    subs = [{"kind": "event", "target": "ev1"}]
    assert (
        due_for_user(
            [ev],
            subs,
            DEFAULT_LEAD_SECONDS,
            target + timedelta(minutes=1),
            lambda k: db.was_sent("u1", k),
        )
        == []
    )


def test_same_round_name_across_legs_does_not_collide(db):
    # A tour event with two legs running an identically-named round.
    target = "2026-09-20T23:59:00+09:00"
    ev = {
        "id": "tour1",
        "name": "Tour",
        "series": ["Liella!"],
        "rounds": [
            {"name": "Official Advance Lottery", "leg": "Kanagawa", "apply_deadline": target},
            {"name": "Official Advance Lottery", "leg": "Aichi", "apply_deadline": target},
        ],
    }
    subs = [{"kind": "event", "target": "tour1"}]
    now = datetime.fromisoformat(target) - timedelta(days=3)
    due = due_for_user([ev], subs, DEFAULT_LEAD_SECONDS, now, lambda k: db.was_sent("u1", k))
    assert len(due) == 2  # both legs fire
    assert len({d.occ_key for d in due}) == 2  # distinct dedup keys
    assert {"Kanagawa", "Aichi"} == {d.round_name.split("·")[-1].strip() for d in due}
    # marking the Kanagawa one sent leaves the Aichi one due
    db.mark_sent("u1", due[0].occ_key, "t")
    again = due_for_user([ev], subs, DEFAULT_LEAD_SECONDS, now, lambda k: db.was_sent("u1", k))
    assert len(again) == 1


def test_series_subscription_matches(db):
    ev = make_event(series=["Aqours"])
    target = datetime.fromisoformat(ev["rounds"][0]["apply_deadline"])
    subs = [{"kind": "series", "target": "Aqours"}]
    due = due_for_user(
        [ev], subs, DEFAULT_LEAD_SECONDS, target - timedelta(days=3), lambda k: db.was_sent("u1", k)
    )
    assert len(due) == 1
    # a different series doesn't match
    assert (
        due_for_user(
            [ev],
            [{"kind": "series", "target": "μ's"}],
            DEFAULT_LEAD_SECONDS,
            target - timedelta(days=3),
            lambda k: db.was_sent("u1", k),
        )
        == []
    )


# ---------- new-event feed ----------


def test_new_event_feed_then_dedup(db):
    ev = make_event(series=["Liella!"])
    subs = [{"kind": "series", "target": "Liella!"}]
    fresh = new_events_for_user([ev], subs, lambda eid: db.was_notified_of_event("u1", eid))
    assert [e["id"] for e in fresh] == ["ev1"]
    db.mark_event_notified("u1", "ev1")
    assert new_events_for_user([ev], subs, lambda eid: db.was_notified_of_event("u1", eid)) == []


# ---------- search ----------


def test_search(db):
    events = [make_event("a", ["Liella!"]), make_event("b", ["Aqours"])]
    events[1]["name"] = "Aqours Finale"
    assert {e["id"] for e in search_events(events, "aqours")} == {"b"}
    assert {e["id"] for e in search_events(events, "伊達")} == {"a", "b"}
