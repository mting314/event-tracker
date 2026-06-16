"""Shared pytest fixtures.

Keep the suite hermetic: strip ambient LLM credentials so any code path that would
otherwise reach a real model (e.g. the ingest English-backfill, or an LLM fallback)
fails fast in ``_model()`` and is handled, instead of making live Vertex calls during
tests. Code that needs to exercise LLM logic injects a fake ``agent=`` instead.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_llm_creds(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)  # default model -> Vertex -> needs the project
