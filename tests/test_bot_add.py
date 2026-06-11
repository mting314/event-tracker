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


def _big_event(n_rounds=40, n_perfs=12):
    return Ingested(
        data={
            "name": "Big Tour " + "ロングネーム" * 10,
            "name_en": "Big Tour",
            "performances": [
                {"date": f"2026-09-{(d % 28) + 1:02d}", "city": "City", "venue": "Venue 会場名"}
                for d in range(n_perfs)
            ],
            "rounds": [
                {
                    "name": f"Round {i} 先行抽選ロングネーム",
                    "leg": "Kanagawa",
                    "apply_deadline": "2026-06-21T23:59:00",
                }
                for i in range(n_rounds)
            ],
        },
        adapter="llm",
        used_llm=True,
    )


async def test_add_posts_embed_view_and_file(interaction):
    with patch.object(bm, "ingest_url", return_value=_ingested()):
        await bm.add.callback(interaction, url="https://lustqueen.info/news/detail/81252")

    interaction.response.defer.assert_awaited_once()
    args, kwargs = interaction.followup.send.call_args
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["file"], discord.File) and kwargs["file"].filename.endswith(".yaml")
    assert isinstance(kwargs["view"], bm.AddConfirmView)
    emb = kwargs["embed"]
    assert isinstance(emb, discord.Embed) and emb.title == "Test 公演"
    assert len(args[0]) <= 2000


async def test_embed_reports_llm_source_in_footer(interaction):
    with patch.object(bm, "ingest_url", return_value=_ingested(used_llm=True, adapter="llm")):
        await bm.add.callback(interaction, url="https://x/2")
    emb = interaction.followup.send.call_args.kwargs["embed"]
    assert "LLM (Vertex)" in emb.footer.text


async def test_embed_fields_within_discord_limits(interaction):
    with patch.object(bm, "ingest_url", return_value=_big_event()):
        await bm.add.callback(interaction, url="https://x/big")
    emb = interaction.followup.send.call_args.kwargs["embed"]
    assert len(emb.title) <= 256
    assert all(len(f.value) <= 1024 for f in emb.fields)


async def test_confirm_opens_pr_for_valid_draft():
    from scrape.util import to_event_yaml

    yaml_text = to_event_yaml(_ingested().data)
    with (
        patch.object(bm, "GITHUB_REPO", "me/event-tracker"),
        patch.object(bm, "GITHUB_TOKEN", "tok"),
        patch("bot.gh.create_event_pr", return_value="https://github.com/me/event-tracker/pull/9"),
    ):
        msg = await bm._confirm_add("2026-test", yaml_text)
    assert "PR opened" in msg and "/pull/9" in msg


async def test_confirm_rejects_invalid_draft():
    bad = "name: Bad\nrounds:\n  - name: no-dates\n    type: presale\n"  # round w/o any date
    with (
        patch.object(bm, "GITHUB_REPO", "me/event-tracker"),
        patch.object(bm, "GITHUB_TOKEN", "tok"),
    ):
        msg = await bm._confirm_add("2026-bad", bad)
    assert "failed validation" in msg


async def test_confirm_without_token_returns_link():
    from scrape.util import to_event_yaml

    yaml_text = to_event_yaml(_ingested().data)
    with (
        patch.object(bm, "GITHUB_REPO", "me/event-tracker"),
        patch.object(bm, "GITHUB_TOKEN", None),
    ):
        msg = await bm._confirm_add("2026-test", yaml_text)
    assert "open it yourself" in msg and "github.com/me/event-tracker/new/" in msg


async def test_add_handles_ingest_failure_gracefully(interaction):
    with patch.object(bm, "ingest_url", side_effect=RuntimeError("boom")):
        await bm.add.callback(interaction, url="https://x/bad")
    interaction.response.defer.assert_awaited_once()
    body = interaction.followup.send.call_args[0][0]
    assert "Couldn't ingest" in body and "boom" in body
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True


async def test_testreminder_dms_the_caller(interaction):
    interaction.user.send = AsyncMock()
    await bm.testreminder.callback(interaction)
    interaction.user.send.assert_awaited_once()
    assert "Test reminder" in interaction.user.send.call_args[0][0]
    assert "DM" in interaction.followup.send.call_args[0][0]


async def test_testreminder_handles_blocked_dms(interaction):
    interaction.user.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "blocked")
    )
    await bm.testreminder.callback(interaction)
    body = interaction.followup.send.call_args[0][0]
    assert "Couldn't DM" in body


async def test_heartbeat_pings_when_url_set():
    with (
        patch.object(bm, "HEALTHCHECK_URL", "https://hc.example/ping"),
        patch.object(bm.requests, "get") as get,
    ):
        await bm._heartbeat()
    get.assert_called_once()


async def test_heartbeat_noop_without_url():
    with patch.object(bm, "HEALTHCHECK_URL", None), patch.object(bm.requests, "get") as get:
        await bm._heartbeat()
    get.assert_not_called()


def test_validate_draft_ok_and_bad():
    from scrape.util import to_event_yaml

    from pydantic import ValidationError

    raw = bm._validate_draft("2026-x", to_event_yaml(_ingested().data))
    assert raw["id"] == "2026-x" and raw["name"] == "Test 公演"
    with pytest.raises(ValidationError):
        bm._validate_draft("2026-x", "name: B\nrounds:\n  - name: no-dates\n")


def test_pr_body_includes_event_details():
    body = bm._pr_body(
        {
            "name": "E",
            "artist": "A",
            "series": ["S"],
            "performances": [{"date": "2026-09-07", "venue": "V"}],
            "rounds": [{"name": "R1", "apply_deadline": "2026-06-21T23:59:00"}],
        }
    )
    assert "Performances" in body and "2026-09-07" in body
    assert "Lottery rounds" in body and "R1" in body


def test_edit_modal_prefills_current_yaml():
    view = bm.AddConfirmView(1, "2026-x", "name: X\nrounds: []\n")
    modal = bm.EditModal(view)
    assert modal.yaml_input.default == "name: X\nrounds: []\n"
