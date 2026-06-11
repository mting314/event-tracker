"""Tests for the Cloud Function's event-YAML validation (offline, no FF/network)."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "functions" / "commit"))
from validate import validate_event_yaml  # noqa: E402

VALID = "name: X\nrounds:\n  - name: R\n    apply_deadline: 2026-06-21T23:59:00\n"


def test_valid_yaml():
    d = validate_event_yaml("2026-x", VALID)
    assert d["name"] == "X"


def test_bad_slug():
    with pytest.raises(ValueError, match="slug"):
        validate_event_yaml("Bad_Slug", VALID)


def test_missing_name():
    with pytest.raises(ValueError, match="name"):
        validate_event_yaml("2026-x", "rounds: []\n")


def test_round_without_date():
    with pytest.raises(ValueError, match="date"):
        validate_event_yaml("2026-x", "name: X\nrounds:\n  - name: R\n")


def test_bad_performance_date():
    with pytest.raises(ValueError, match="performance date"):
        validate_event_yaml("2026-x", "name: X\nperformances:\n  - date: Sept 7\n")
