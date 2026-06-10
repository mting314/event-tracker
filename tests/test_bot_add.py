"""Async tests for the /add Discord command.

Follows the offkai-bot pattern: mock discord.Interaction (AsyncMock response /
followup), patch the heavy ingest call, invoke the command's .callback directly.
No Discord connection or network.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

os.environ.setdefault("DB_PATH", "/tmp/lltracker_test.db")  # keep the repo db clean
import bot.main as bm  # noqa: E402
from scrape.ingest import Ingested  # noqa: E402


@pytest.fixture
def interaction():
    i = MagicMock(spec=discord.Interaction)
    i.response = MagicMock(defer=AsyncMock())
    i.followup = MagicMock(send=AsyncMock())
    return i


def _ingested(used_llm=False, adapter="generic"):
    return Ingested(
        data={
            "name": "Test 公演",
            "performances": [{"date": "2026-09-07", "venue": "Shangri-La"}],
            "rounds": [{"name": "FC先行", "apply_deadline": "2026-06-21T23:59:00"}],
        },
        adapter=adapter,
        used_llm=used_llm,
    )


async def test_add_defers_and_posts_draft_file(interaction):
    with patch.object(bm, "ingest_url", return_value=_ingested()):
        await bm.add.callback(interaction, url="https://lustqueen.info/news/detail/81252")

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    args, kwargs = interaction.followup.send.call_args
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["file"], discord.File)  # YAML attached
    assert kwargs["file"].filename.endswith(".yaml")
    assert "Test 公演" in args[0] and "generic" in args[0]  # summary text


async def test_add_includes_pr_link_when_repo_set(interaction):
    with (
        patch.object(bm, "GITHUB_REPO", "me/event-tracker"),
        patch.object(bm, "ingest_url", return_value=_ingested()),
    ):
        await bm.add.callback(interaction, url="https://x/1")
    body = interaction.followup.send.call_args[0][0]
    assert "github.com/me/event-tracker/new/" in body and "Open a PR" in body


async def test_add_reports_llm_fallback(interaction):
    with patch.object(bm, "ingest_url", return_value=_ingested(used_llm=True, adapter="llm")):
        await bm.add.callback(interaction, url="https://x/2")
    assert "LLM (Vertex)" in interaction.followup.send.call_args[0][0]


async def test_add_handles_ingest_failure_gracefully(interaction):
    with patch.object(bm, "ingest_url", side_effect=RuntimeError("boom")):
        await bm.add.callback(interaction, url="https://x/bad")
    interaction.response.defer.assert_awaited_once()
    body = interaction.followup.send.call_args[0][0]
    assert "Couldn't ingest" in body and "boom" in body
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
