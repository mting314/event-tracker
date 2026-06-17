"""Tests for the Upcoming-index group builder.

Focus: the per-event ``windows`` dataset that drives the "has open round" filter.
Each round is encoded as ``"<apply_open>~<close>"`` where the close is the round's
real deadline, or — for a deadline-less first-come sale — the show day, so such a
sale counts as open while it's live but ages out after the performance (instead of
staying open forever).
"""

from build.build_site import build_index_groups
from schema.models import Event


def _event(rounds, date="2026-08-01"):
    return Event.model_validate(
        {
            "id": "ev",
            "name": "E",
            "kind": "concert",
            "performances": [
                {"date": date, "venue": "V", "rounds": rounds},
            ],
        }
    )


def test_windows_encode_open_and_close_per_round():
    ev = _event(
        [
            # lottery: full window — close is the real deadline
            {
                "name": "lottery",
                "apply_open": "2026-06-15T18:00:00",
                "apply_deadline": "2026-06-28T23:59:00",
            },
            # first-come sale: open only, no deadline
            {"name": "first-come", "apply_open": "2026-06-13T12:00:00"},
            # legacy round: deadline only, no open
            {"name": "deadline-only", "apply_deadline": "2026-07-01T23:59:00"},
        ]
    )
    windows = build_index_groups([ev])[0]["windows"]
    parsed = {w.split("~")[0]: w.split("~")[1] for w in windows}

    # lottery -> close is its real deadline
    assert parsed["2026-06-15T18:00:00+09:00"].startswith("2026-06-28T23:59:00")
    # deadline-only -> open empty, close is the deadline (back-compat with old behavior)
    assert parsed[""].startswith("2026-07-01T23:59:00")
    # first-come -> open set, close is the SHOW DAY (not empty) so it ages out
    assert parsed["2026-06-13T12:00:00+09:00"].startswith("2026-08-01T23:59:59")


def test_first_come_close_falls_to_each_shows_own_date():
    # A tour-wide first-come round repeated under two shows closes per-show.
    ev = Event.model_validate(
        {
            "id": "ev",
            "name": "E",
            "kind": "concert",
            "performances": [
                {
                    "date": "2026-08-01",
                    "venue": "V",
                    "rounds": [{"name": "fc", "apply_open": "2026-06-13T12:00:00"}],
                },
                {
                    "date": "2026-09-09",
                    "venue": "V",
                    "rounds": [{"name": "fc", "apply_open": "2026-06-13T12:00:00"}],
                },
            ],
        }
    )
    closes = sorted(w.split("~")[1][:10] for w in build_index_groups([ev])[0]["windows"])
    assert closes == ["2026-08-01", "2026-09-09"]


def test_shows_lists_every_performance_day_end_of_day():
    # `shows` is authoritative for past vs upcoming — one end-of-day JST instant per
    # performance date, sorted, so the client can check "is the last show over?".
    ev = Event.model_validate(
        {
            "id": "ev",
            "name": "E",
            "kind": "concert",
            "performances": [
                {"date": "2026-08-02", "venue": "V"},
                {"date": "2026-08-01", "venue": "V"},
            ],
        }
    )
    shows = build_index_groups([ev])[0]["shows"]
    assert shows == ["2026-08-01T23:59:59+09:00", "2026-08-02T23:59:59+09:00"]


def test_windows_omit_rounds_with_no_application_window():
    # A round with neither apply_open nor apply_deadline contributes no window
    # (it can't be "open" by date), but a results_date keeps it a valid round.
    ev = _event([{"name": "results-only", "results_date": "2026-07-02T18:00:00"}])
    assert build_index_groups([ev])[0]["windows"] == []
