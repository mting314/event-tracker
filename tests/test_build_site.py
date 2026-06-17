"""Tests for the Upcoming-index group builder.

Focus: the per-event ``windows`` dataset that drives the "has open round" filter
must represent each round's *application window* (apply_open + apply_deadline),
so a deadline-less first-come sale still counts as open while it's live — not
just rounds that carry a deadline.
"""

from build.build_site import build_index_groups
from schema.models import Event


def _event(rounds):
    return Event.model_validate(
        {
            "id": "ev",
            "name": "E",
            "kind": "concert",
            "performances": [
                {"date": "2026-08-01", "venue": "V", "rounds": rounds},
            ],
        }
    )


def test_windows_encode_open_and_deadline_per_round():
    ev = _event(
        [
            # lottery: full window (open + deadline)
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

    parsed = [w.split("~") for w in windows]
    # lottery -> both sides populated
    assert any(o and d for o, d in parsed), windows
    # first-come -> open populated, deadline empty (the case that used to be dropped)
    assert any(o and not d for o, d in parsed), windows
    # deadline-only -> open empty, deadline populated (back-compat with old behavior)
    assert any(not o and d for o, d in parsed), windows


def test_windows_omit_rounds_with_no_application_window():
    # A round with neither apply_open nor apply_deadline contributes no window
    # (it can't be "open" by date), but a results_date keeps it a valid round.
    ev = _event([{"name": "results-only", "results_date": "2026-07-02T18:00:00"}])
    assert build_index_groups([ev])[0]["windows"] == []
