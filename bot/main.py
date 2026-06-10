"""Discord bot entrypoint (discord.py).

Slash commands let you search events and subscribe to specific events or whole
series; a background loop DMs you (and optionally posts to a channel) before each
tracked date. Run with environment variables:

    DISCORD_TOKEN     bot token (required)
    EVENTS_SOURCE     events.json URL or path (default: local data/events.json)
    DB_PATH           sqlite path (default: bot/tracker.db)
    SITE_URL          base site URL for links (optional)
    CHECK_INTERVAL_MIN  scheduler cadence in minutes (default 15)

    python -m bot.main
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import certifi
import discord
import requests
from discord import app_commands
from discord.ext import tasks

from scrape.ingest import ingest_url
from scrape.util import slugify, to_event_yaml

from .db import DB, DEFAULT_DB
from .reminders import (
    DEFAULT_LEAD_SECONDS,
    DueReminder,
    due_for_user,
    format_reminder,
    humanize,
    new_events_for_user,
)
from .sync import all_series, load_events, search_events

try:  # load .env for local runs (containers inject env directly); optional dep
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Verify TLS using the OS trust store so it works behind corporate TLS interception
# / proxies (managed laptops present Discord's cert signed by an internal root CA that
# certifi's public bundle doesn't have). Fall back to certifi if truststore is absent.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 - fall back to a public CA bundle
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())

JST = timezone(timedelta(hours=9))
EVENTS_SOURCE = os.environ.get("EVENTS_SOURCE")
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
CHECK_INTERVAL_MIN = int(os.environ.get("CHECK_INTERVAL_MIN", "15"))
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")  # set for instant per-guild command sync
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL")  # dead-man's-switch ping each tick
log = logging.getLogger("bot")

db = DB(os.environ.get("DB_PATH") or DEFAULT_DB)
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
_events_cache: list[dict] = []


def refresh_events():
    global _events_cache
    try:
        _events_cache = load_events(EVENTS_SOURCE)
    except Exception as exc:  # keep last good cache on a transient failure
        print(f"⚠️ events refresh failed: {exc}")
    return _events_cache


def event_link(eid: str) -> str:
    return f"{SITE_URL}/event/{eid}.html" if SITE_URL else eid


def parse_lead_spec(spec: str) -> list[int]:
    """'3d,1d,2h,30m' -> [seconds...]."""
    units = {"d": 86400, "h": 3600, "m": 60}
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        out.append(int(part[:-1]) * units[part[-1].lower()])
    return sorted(set(out), reverse=True)


# ---------------- slash commands ----------------


@tree.command(description="Search tracked events")
@app_commands.describe(query="name, series, venue or performer")
async def search(interaction: discord.Interaction, query: str):
    results = search_events(_events_cache or refresh_events(), query)
    if not results:
        await interaction.response.send_message(f"No events match “{query}”.", ephemeral=True)
        return
    lines = [f"• **{e['name']}** — `{e['id']}` ({', '.join(e.get('series', []))})" for e in results]
    lines.append("\nSubscribe with `/subscribe event <id>` or `/subscribe series <name>`.")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


GITHUB_REPO = os.environ.get("GITHUB_REPO")  # owner/repo, for the "Open a PR" link
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")


@tree.command(description="Draft an event from any URL (official, FC, live-house, …)")
@app_commands.describe(url="event page URL", llm="force LLM (Vertex) extraction")
async def add(interaction: discord.Interaction, url: str, llm: bool = False):
    await interaction.response.defer(ephemeral=True, thinking=True)
    log.info("/add user=%s url=%s force_llm=%s", interaction.user, url, llm)
    try:  # fetch + parse off the event loop (blocking I/O + optional LLM)
        res = await asyncio.to_thread(ingest_url, url, True, llm)
    except Exception as exc:  # noqa: BLE001
        log.exception("/add failed url=%s", url)
        await interaction.followup.send(f"⚠️ Couldn't ingest that URL: {exc}", ephemeral=True)
        return
    log.info("/add done url=%s adapter=%s used_llm=%s rounds=%d", url, res.adapter, res.used_llm,
             len(res.data.get("rounds", [])))

    data = res.data
    dates = [p["date"] for p in data.get("performances", []) if p.get("date")]
    slug = slugify(data.get("name") or "", dates)
    yaml_text = to_event_yaml(data)
    src = "LLM (Vertex)" if res.used_llm else f"`{res.adapter}`"
    lines = [
        f"**{data.get('name', '(no name)')}**",
        f"via {src} · {len(data.get('performances', []))} performances · "
        f"{len(data.get('rounds', []))} rounds → `events/{slug}.yaml`",
        "Review the attached draft, then commit it (lottery dates are best-effort — verify).",
    ]
    if GITHUB_REPO:
        link = (
            f"https://github.com/{GITHUB_REPO}/new/{GITHUB_BRANCH}"
            f"?filename=events/{slug}.yaml&value={quote(yaml_text)}"
        )
        lines.append(
            f"[→ Open a PR with this draft]({link})"
            if len(link) < 7800
            else "_(too long for a PR link — use the attached file)_"
        )
    file = discord.File(io.BytesIO(yaml_text.encode("utf-8")), filename=f"{slug}.yaml")
    await interaction.followup.send("\n".join(lines), file=file, ephemeral=True)


subscribe = app_commands.Group(name="subscribe", description="Subscribe to events or series")
unsubscribe = app_commands.Group(name="unsubscribe", description="Remove a subscription")
tree.add_command(subscribe)
tree.add_command(unsubscribe)


@subscribe.command(name="event", description="Get reminders for one event")
async def subscribe_event(interaction: discord.Interaction, event_id: str):
    ev = next((e for e in (_events_cache or refresh_events()) if e["id"] == event_id), None)
    if not ev:
        await interaction.response.send_message(f"Unknown event id `{event_id}`.", ephemeral=True)
        return
    ok = db.add_subscription(str(interaction.user.id), "event", event_id)
    db.mark_event_notified(str(interaction.user.id), event_id)  # don't "new-event" ping for it
    msg = f"✅ Subscribed to **{ev['name']}**." if ok else "Already subscribed."
    await interaction.response.send_message(msg, ephemeral=True)


@subscribe.command(name="series", description="Get reminders for all events in a series")
async def subscribe_series(interaction: discord.Interaction, series: str):
    ok = db.add_subscription(str(interaction.user.id), "series", series)
    await interaction.response.send_message(
        f"✅ Subscribed to series **{series}**." if ok else "Already subscribed.", ephemeral=True
    )


@subscribe_series.autocomplete("series")
async def _series_ac(interaction: discord.Interaction, current: str):
    opts = [
        s for s in all_series(_events_cache or refresh_events()) if current.lower() in s.lower()
    ]
    return [app_commands.Choice(name=s, value=s) for s in opts[:25]]


@unsubscribe.command(name="event", description="Stop reminders for one event")
async def unsubscribe_event(interaction: discord.Interaction, event_id: str):
    ok = db.remove_subscription(str(interaction.user.id), "event", event_id)
    await interaction.response.send_message(
        "✅ Removed." if ok else "You weren't subscribed.", ephemeral=True
    )


@unsubscribe.command(name="series", description="Stop reminders for a series")
async def unsubscribe_series(interaction: discord.Interaction, series: str):
    ok = db.remove_subscription(str(interaction.user.id), "series", series)
    await interaction.response.send_message(
        "✅ Removed." if ok else "You weren't subscribed.", ephemeral=True
    )


@tree.command(description="List your subscriptions")
async def subscriptions(interaction: discord.Interaction):
    subs = db.list_subscriptions(str(interaction.user.id))
    if not subs:
        await interaction.response.send_message(
            "No subscriptions yet. Try `/search`.", ephemeral=True
        )
        return
    lines = [f"• {s['kind']}: **{s['target']}**" for s in subs]
    await interaction.response.send_message(
        "Your subscriptions:\n" + "\n".join(lines), ephemeral=True
    )


@tree.command(description="Your upcoming application dates")
async def upcoming(interaction: discord.Interaction):
    subs = db.list_subscriptions(str(interaction.user.id))
    now = datetime.now(JST)
    from .reminders import DATE_LABELS, occurrences

    rows = []
    for ev, rnd, dtype, target in occurrences(_events_cache or refresh_events()):
        if target <= now:
            continue
        if any(
            (s["kind"] == "event" and s["target"] == ev["id"])
            or (s["kind"] == "series" and s["target"] in ev.get("series", []))
            for s in subs
        ):
            rows.append((target, ev, rnd, dtype))
    rows.sort(key=lambda r: r[0])
    if not rows:
        await interaction.response.send_message(
            "Nothing upcoming in your subscriptions.", ephemeral=True
        )
        return
    lines = [
        f"• {t.strftime('%m-%d %H:%M')} — **{ev['name']}** {rnd['name']} ({DATE_LABELS[dt].split()[0]})"
        for t, ev, rnd, dt in rows[:15]
    ]
    await interaction.response.send_message("Upcoming:\n" + "\n".join(lines), ephemeral=True)


@tree.command(description="View or change your reminder settings")
@app_commands.describe(lead_times="e.g. 3d,1d,2h", dm="receive DMs?")
async def settings(
    interaction: discord.Interaction, lead_times: str | None = None, dm: bool | None = None
):
    uid = str(interaction.user.id)
    if lead_times is not None or dm is not None:
        leads = parse_lead_spec(lead_times) if lead_times else None
        db.set_settings(uid, lead_times=leads, dm_enabled=dm)
    s = db.get_settings(uid, DEFAULT_LEAD_SECONDS)
    await interaction.response.send_message(
        f"Lead times: {', '.join(humanize(x) for x in s['lead_times'])} · DM: {s['dm_enabled']}",
        ephemeral=True,
    )


@tree.command(description="(Admin) Post reminders to this channel too")
async def setchannel(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Run this in a server channel.", ephemeral=True)
        return
    db.set_channel(str(interaction.guild_id), str(interaction.channel_id))
    await interaction.response.send_message(
        "✅ This channel will receive reminders.", ephemeral=True
    )


@tree.command(description="(Admin) DM yourself a sample reminder to verify delivery")
@app_commands.default_permissions(manage_guild=True)
async def testreminder(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    sample = DueReminder(
        user_id=str(interaction.user.id),
        event_id="test-event",
        event_name="Test Event 🎫",
        round_name="Sample Round",
        date_type="apply_deadline",
        target=datetime.now(JST) + timedelta(days=3),
        lead=3 * 86400,
        occ_key="test",
    )
    text = "✅ **Test reminder** — DMs from this bot are working!\n\n" + format_reminder(sample)
    try:
        await interaction.user.send(text)
        await interaction.followup.send("Sent you a DM ✅", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ Couldn't DM you. Enable **Privacy Settings → Direct Messages from "
            "server members** for this server, then retry.",
            ephemeral=True,
        )
    except Exception as exc:  # noqa: BLE001
        await interaction.followup.send(f"⚠️ DM failed: {exc}", ephemeral=True)


# ---------------- scheduler ----------------


@tasks.loop(minutes=CHECK_INTERVAL_MIN)
async def scheduler():
    events = refresh_events()
    now = datetime.now(JST)
    channels = db.all_channels()
    for uid, subs in db.all_subscriptions().items():
        s = db.get_settings(uid, DEFAULT_LEAD_SECONDS)
        # deadline reminders
        for r in due_for_user(
            events, subs, s["lead_times"], now, lambda k, uid=uid: db.was_sent(uid, k)
        ):
            r.user_id = uid
            text = format_reminder(r) + (f"\n{event_link(r.event_id)}" if SITE_URL else "")
            await _deliver(uid, text, s["dm_enabled"], channels)
            db.mark_sent(uid, r.occ_key, now.isoformat())
            for k in r.suppress_keys:
                db.mark_sent(uid, k, now.isoformat())
        # new-event-in-series feed
        for ev in new_events_for_user(
            events, subs, lambda eid, uid=uid: db.was_notified_of_event(uid, eid)
        ):
            await _deliver(
                uid,
                f"🆕 New event in a series you follow: **{ev['name']}**\n{event_link(ev['id'])}",
                s["dm_enabled"],
                channels,
            )
            db.mark_event_notified(uid, ev["id"])
    await _heartbeat()  # signal "tick completed" to an uptime monitor


async def _heartbeat():
    """Ping HEALTHCHECK_URL after a successful tick (dead-man's switch). If pings
    stop (bot stuck / container or VM down), the monitor alerts. No-op if unset."""
    if not HEALTHCHECK_URL:
        return
    try:
        await asyncio.to_thread(requests.get, HEALTHCHECK_URL, timeout=10)
    except Exception as exc:  # noqa: BLE001 - never let monitoring break the loop
        print(f"⚠️ heartbeat failed: {exc}")


async def _deliver(uid: str, text: str, dm_enabled: bool, channels: list[str]):
    if dm_enabled:
        try:
            user = await client.fetch_user(int(uid))
            await user.send(text)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ DM to {uid} failed: {exc}")
    for cid in channels:
        ch = client.get_channel(int(cid))
        if ch:
            try:
                await ch.send(f"<@{uid}> {text}")
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ channel {cid} post failed: {exc}")


@client.event
async def on_ready():
    refresh_events()
    if GUILD_ID:  # instant: commands appear immediately in this guild (no ~1h global wait)
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:  # global sync — propagates to all servers within ~1h
        await tree.sync()
    if not scheduler.is_running():
        scheduler.start()
    print(f"Logged in as {client.user} · {len(_events_cache)} events loaded")


def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("set DISCORD_TOKEN")
    # root_logger=True so our scrape/ingest/llm INFO logs surface (not just discord's)
    client.run(token, root_logger=True)


if __name__ == "__main__":
    main()
