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
    i.response = MagicMock(defer=AsyncMock(), send_message=AsyncMock())
    i.followup = MagicMock(send=AsyncMock())
    i.edit_original_response = AsyncMock()
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
    kwargs = interaction.edit_original_response.call_args.kwargs  # final edit = the review
    f = kwargs["attachments"][0]
    assert isinstance(f, discord.File) and f.filename.endswith(".yaml")
    assert isinstance(kwargs["view"], bm.AddConfirmView)
    emb = kwargs["embed"]
    assert isinstance(emb, discord.Embed) and emb.title == "Test 公演"
    assert len(kwargs["content"]) <= 2000


async def test_add_streams_progress_status(interaction):
    """The deferred reply is updated with a fetching status before the result."""
    with patch.object(bm, "ingest_url", return_value=_ingested()):
        await bm.add.callback(interaction, url="https://x/slow")
    # first edit is the fetch status; last edit carries the embed
    first = interaction.edit_original_response.call_args_list[0].kwargs["content"]
    assert "Fetching" in first
    assert interaction.edit_original_response.call_args.kwargs["embed"].title == "Test 公演"


async def test_embed_reports_llm_source_in_footer(interaction):
    with patch.object(bm, "ingest_url", return_value=_ingested(used_llm=True, adapter="llm")):
        await bm.add.callback(interaction, url="https://x/2")
    emb = interaction.edit_original_response.call_args.kwargs["embed"]
    assert "LLM (Vertex)" in emb.footer.text


async def test_embed_fields_within_discord_limits(interaction):
    with patch.object(bm, "ingest_url", return_value=_big_event()):
        await bm.add.callback(interaction, url="https://x/big")
    emb = interaction.edit_original_response.call_args.kwargs["embed"]
    assert len(emb.title) <= 256
    assert all(len(f.value) <= 1024 for f in emb.fields)


def test_fmt_dt_accepts_str_and_datetime():
    from datetime import date, datetime

    assert bm._fmt_dt("2026-06-21T23:59:00") == "2026-06-21 23:59"
    assert bm._fmt_dt(datetime(2026, 6, 21, 23, 59)) == "2026-06-21 23:59"
    assert bm._fmt_dt(date(2026, 6, 21)) == "2026-06-21"
    assert bm._fmt_dt(None) == "—"


def test_embed_handles_generic_adapter_datetimes():
    """Generic adapter yields datetime objects for round dates (not ISO strings);
    the embed must render them without crashing (regression for the /add hang)."""
    from datetime import datetime

    data = {
        "name": "Lust Queen 公演",
        "performances": [{"date": "2026-09-07", "venue": "Shangri-La"}],
        "rounds": [
            {
                "name": "FC先行",
                "type": "presale",
                "apply_open": datetime(2026, 6, 5, 12, 0),
                "apply_deadline": datetime(2026, 6, 21, 23, 59),
            }
        ],
    }
    emb = bm.build_event_embed(data, "2026-x", "generic")  # must not raise
    rounds = next(f for f in emb.fields if f.name.startswith("Lottery rounds"))
    assert "2026-06-21 23:59" in rounds.value


def test_embed_distinguishes_same_venue_performances():
    data = {
        "name": "Unit Fan Meeting",
        "performances": [
            {
                "date": "2026-09-05",
                "city": "大分",
                "venue": "iichikoグランシアタ",
                "label": "昼公演",
                "starts": "13:00",
            },
            {
                "date": "2026-09-05",
                "city": "大分",
                "venue": "iichikoグランシアタ",
                "label": "夜公演",
                "starts": "17:00",
            },
        ],
        "rounds": [{"name": "FC", "apply_deadline": "2026-06-21T23:59:00"}],
    }
    emb = bm.build_event_embed(data, "2026-x", "generic")
    perf = next(f for f in emb.fields if f.name.startswith("Performances"))
    assert "昼公演" in perf.value and "夜公演" in perf.value
    assert "開演13:00" in perf.value and "開演17:00" in perf.value
    lines = [ln for ln in perf.value.splitlines() if ln.strip()]
    assert lines[0] != lines[1]  # no longer identical


async def test_confirm_saves_valid_draft_to_main():
    from scrape.util import to_event_yaml

    yaml_text = to_event_yaml(_ingested().data)
    with (
        patch.object(bm, "GITHUB_REPO", "me/event-tracker"),
        patch.object(bm, "GITHUB_TOKEN", "tok"),
        patch(
            "bot.gh.commit_to_main", return_value="https://github.com/me/event-tracker/commit/abc"
        ),
    ):
        msg = await bm._confirm_add("2026-test", yaml_text)
    assert "Saved" in msg and "/commit/abc" in msg


async def test_confirm_rejects_invalid_draft():
    bad = "name: Bad\nrounds:\n  - name: no-dates\n    type: presale\n"  # round w/o any date
    with (
        patch.object(bm, "GITHUB_REPO", "me/event-tracker"),
        patch.object(bm, "GITHUB_TOKEN", "tok"),
    ):
        msg = await bm._confirm_add("2026-bad", bad)
    assert "failed validation" in msg


async def test_confirm_without_token_reports_missing_config():
    from scrape.util import to_event_yaml

    yaml_text = to_event_yaml(_ingested().data)
    with (
        patch.object(bm, "GITHUB_REPO", "me/event-tracker"),
        patch.object(bm, "GITHUB_TOKEN", None),
    ):
        msg = await bm._confirm_add("2026-test", yaml_text)
    assert "GITHUB_TOKEN" in msg and "can't save" in msg


def test_ingest_progress_callback_reports_ai_step():
    from scrape import ingest

    msgs = []
    with patch("scrape.llm.scrape", return_value={"name": "X", "rounds": []}):
        ingest.ingest_url("https://x/1", force_llm=True, progress=msgs.append)
    assert any("AI" in m for m in msgs)  # the LLM step is announced


async def test_add_handles_ingest_failure_gracefully(interaction):
    with patch.object(bm, "ingest_url", side_effect=RuntimeError("boom")):
        await bm.add.callback(interaction, url="https://x/bad")
    interaction.response.defer.assert_awaited_once()
    body = interaction.edit_original_response.call_args.kwargs["content"]
    assert "Couldn't ingest" in body and "boom" in body


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
    from pydantic import ValidationError

    from scrape.util import to_event_yaml

    raw = bm._validate_draft("2026-x", to_event_yaml(_ingested().data))
    assert raw["id"] == "2026-x" and raw["name"] == "Test 公演"
    with pytest.raises(ValidationError):
        bm._validate_draft("2026-x", "name: B\nrounds:\n  - name: no-dates\n")


def test_edit_modal_prefills_current_yaml():
    view = bm.AddConfirmView(1, "2026-x", "name: X\nrounds: []\n")
    modal = bm.EditModal(view)
    assert modal.yaml_input.default == "name: X\nrounds: []\n"


async def test_subscribe_event_autocomplete_filters_by_name_or_id():
    evs = [
        {"id": "2026-liella-7th", "name": "Liella 7th", "series": []},
        {"id": "2026-gkss", "name": "GKSS VS", "series": []},
    ]
    with patch.object(bm, "_events_cache", evs):
        choices = await bm._event_ac(MagicMock(), "liella")
    assert len(choices) == 1 and choices[0].value == "2026-liella-7th"
    assert "Liella 7th" in choices[0].name


async def test_subscriptions_resolves_ids_to_names(interaction):
    subs = [
        {"kind": "event", "target": "2026-liella-7th"},
        {"kind": "series", "target": "Hasunosora"},
    ]
    evs = [{"id": "2026-liella-7th", "name": "Liella! 7th Live", "series": []}]
    with (
        patch.object(bm, "_events_cache", evs),
        patch.object(bm.db, "list_subscriptions", return_value=subs),
    ):
        await bm.subscriptions.callback(interaction)
    body = interaction.response.send_message.call_args[0][0]
    assert "Liella! 7th Live" in body  # name shown, not the slug
    assert "2026-liella-7th" not in body
    assert "Hasunosora" in body
    assert "**Events**" in body and "**Series**" in body


async def test_subscribe_autocomplete_label_has_no_slug():
    evs = [{"id": "2026-liella-7th", "name": "Liella! 7th Live", "series": []}]
    with patch.object(bm, "_events_cache", evs):
        choices = await bm._event_ac(MagicMock(), "liella")
    assert choices[0].name == "Liella! 7th Live"  # human name only
    assert choices[0].value == "2026-liella-7th"  # slug still the stored value


async def test_deadlines_lists_future_dates_only(interaction):
    ev = {
        "id": "2026-x",
        "name": "X Tour",
        "series": [],
        "official_url": "https://example.com/x-tour",
        "rounds": [
            {"name": "Past FC", "apply_deadline": "2020-01-01T23:59:00+09:00"},
            {
                "name": "FC先行",
                "leg": "Tokyo",
                "apply_deadline": "2030-06-21T23:59:00+09:00",
                "results_date": "2030-07-01T12:00:00+09:00",
                "apply_url": "https://apply.example.com/fc",
            },
        ],
    }
    with patch.object(bm, "_events_cache", [ev]):
        await bm.deadlines.callback(interaction, event="2026-x")
    emb = interaction.response.send_message.call_args.kwargs["embed"]
    body = emb.description
    assert "X Tour" in emb.title
    assert emb.url == "https://example.com/x-tour"  # title links to the official page
    assert "FC先行 · Tokyo" in body
    assert "🔴 deadline" in body  # emoji tag for apply_deadline
    assert "<t:1908284340:f>" in body  # 2030-06-21T23:59:00+09:00 as a Discord timestamp
    assert body.count("[apply](https://apply.example.com/fc)") == 1  # only on the deadline row
    assert "🎯 results" in body  # results row present...
    deadline_line = next(ln for ln in body.splitlines() if "🔴 deadline" in ln)
    results_line = next(ln for ln in body.splitlines() if "🎯 results" in ln)
    assert "[apply]" in deadline_line and "[apply]" not in results_line  # ...but no apply link
    assert "Past FC" not in body  # past dates filtered out


def test_event_official_link_prefers_official_then_falls_back():
    assert (
        bm.event_official_link(
            {"id": "x", "official_url": "https://off", "eventernote_url": "https://en"}
        )
        == "https://off"
    )
    assert bm.event_official_link({"id": "x", "eventernote_url": "https://en"}) == "https://en"
    assert bm.event_official_link({"id": "x", "source_url": "https://src"}) == "https://src"
    with patch.object(bm, "SITE_URL", "https://site"):
        assert bm.event_official_link({"id": "2026-x"}) == "https://site/event/2026-x.html"


def test_discord_ts_renders_dynamic_timestamp():
    from datetime import datetime

    from bot.reminders import discord_ts

    dt = datetime.fromisoformat("2030-06-21T23:59:00+09:00")
    assert discord_ts(dt, "R") == "<t:1908284340:R>"
    assert discord_ts(dt) == "<t:1908284340:f>"


def test_date_tag_has_distinct_emoji_per_type():
    from bot.reminders import date_tag

    tags = {
        dt: date_tag(dt)
        for dt in ("apply_open", "apply_deadline", "results_date", "payment_deadline")
    }
    assert tags["apply_open"] == "🟢 opens"
    assert tags["apply_deadline"] == "🔴 deadline"
    assert tags["results_date"] == "🎯 results"
    assert tags["payment_deadline"] == "💰 payment"
    emojis = [t.split()[0] for t in tags.values()]
    assert len(set(emojis)) == 4  # all distinct


def test_apply_link_only_on_deadline_with_url():
    rnd = {"apply_url": "https://a.com"}
    assert bm._apply_link(rnd, "apply_deadline") == " · [apply](https://a.com)"
    # not on results/payment/open rows, even with a url
    assert bm._apply_link(rnd, "results_date") == ""
    assert bm._apply_link(rnd, "payment_deadline") == ""
    assert bm._apply_link(rnd, "apply_open") == ""
    # no url -> no link
    assert bm._apply_link({"name": "no url"}, "apply_deadline") == ""


async def test_upcoming_embeds_apply_links(interaction):
    ev = {
        "id": "2026-x",
        "name": "X Tour",
        "series": [],
        "rounds": [
            {
                "name": "FC先行",
                "apply_deadline": "2030-06-21T23:59:00+09:00",
                "apply_url": "https://apply.example.com/fc",
            },
        ],
    }
    subs = [{"kind": "event", "target": "2026-x"}]
    with (
        patch.object(bm, "_events_cache", [ev]),
        patch.object(bm.db, "list_subscriptions", return_value=subs),
    ):
        await bm.upcoming.callback(interaction)
    emb = interaction.response.send_message.call_args.kwargs["embed"]
    assert "X Tour" in emb.description
    assert "[apply](https://apply.example.com/fc)" in emb.description


async def test_deadlines_unknown_event(interaction):
    with patch.object(bm, "_events_cache", []):
        await bm.deadlines.callback(interaction, event="nope")
    assert "Unknown event" in interaction.response.send_message.call_args[0][0]


async def test_deadlines_no_upcoming(interaction):
    ev = {
        "id": "2026-x",
        "name": "X Tour",
        "series": [],
        "rounds": [{"name": "Past", "apply_deadline": "2020-01-01T00:00:00+09:00"}],
    }
    with patch.object(bm, "_events_cache", [ev]):
        await bm.deadlines.callback(interaction, event="2026-x")
    assert "No upcoming" in interaction.response.send_message.call_args[0][0]


async def test_delete_event_commits():
    with (
        patch.object(bm, "GITHUB_REPO", "me/x"),
        patch.object(bm, "GITHUB_TOKEN", "tok"),
        patch("bot.gh.delete_from_main", return_value="https://github.com/me/x/commit/del"),
    ):
        msg = await bm._delete_event("2026-x")
    assert "Deleted" in msg and "/commit/del" in msg


async def test_delete_event_without_config():
    with patch.object(bm, "GITHUB_REPO", None), patch.object(bm, "GITHUB_TOKEN", None):
        msg = await bm._delete_event("2026-x")
    assert "can't delete" in msg


async def test_delete_command_prompts_confirm(interaction):
    with patch.object(bm, "_events_cache", [{"id": "2026-x", "name": "X Tour", "series": []}]):
        await bm.delete.callback(interaction, event="2026-x")
    args, kwargs = interaction.response.send_message.call_args
    assert "X Tour" in args[0] and isinstance(kwargs["view"], bm.DeleteConfirmView)
