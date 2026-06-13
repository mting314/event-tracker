"""Discord bot entrypoint (discord.py).

Slash commands let you search events and subscribe to specific events or whole
series; a background loop DMs you (and optionally posts to a channel) before each
tracked date. Run with environment variables:

    DISCORD_TOKEN     bot token (required)
    EVENTS_SOURCE     events.json URL or path (default: local artifact, else compiled from events/*.yaml)
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

import certifi
import discord
import requests
from discord import app_commands
from discord.ext import tasks

from schema.models import nest_rounds
from scrape.ingest import ingest_url
from scrape.util import slugify, to_event_yaml

from .db import DB, DEFAULT_DB
from .reminders import (
    DEFAULT_LEAD_SECONDS,
    DueReminder,
    discord_ts,
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


def event_name(eid: str) -> str:
    """Resolve an event id (slug) to its human-readable name; fall back to the id."""
    for e in _events_cache or refresh_events():
        if e["id"] == eid:
            return e["name"]
    return eid


def event_official_link(ev: dict) -> str:
    """Best external link for an event: official page, else eventernote, else the
    ingest source, else the site's own event page."""
    return (
        ev.get("official_url")
        or ev.get("eventernote_url")
        or ev.get("source_url")
        or event_link(ev["id"])
    )


def _apply_link(rnd: dict, date_type: str) -> str:
    """A ` · [apply](url)` masked link, only on the application-deadline row (where
    applying is the action) and only if the round has an apply_url. Returns '' for
    results/payment/open rows. Masked links render only inside embeds."""
    if date_type != "apply_deadline" or not rnd.get("apply_url"):
        return ""
    return f" · [apply]({rnd['apply_url']})"


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


@tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    """Global safety net: any uncaught command error is surfaced to the user
    (ephemerally) instead of leaving the 'is thinking…' spinner hanging."""
    orig = getattr(error, "original", error)
    cmd = interaction.command.qualified_name if interaction.command else "?"
    log.exception("unhandled error in /%s", cmd, exc_info=orig)
    msg = f"⚠️ Something went wrong running `/{cmd}`: {orig}"[:1900]
    try:
        if interaction.response.is_done():
            # deferred or already replied — replace the spinner if we can
            try:
                await interaction.edit_original_response(content=msg)
            except discord.HTTPException:
                await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:  # noqa: BLE001 - last resort; don't mask the original error
        log.exception("failed to surface command error to the user")


@tree.command(description="Search tracked events")
@app_commands.describe(query="name, series, venue or performer")
async def search(interaction: discord.Interaction, query: str):
    results = search_events(_events_cache or refresh_events(), query)
    if not results:
        await interaction.response.send_message(f"No events match “{query}”.", ephemeral=True)
        return
    lines = [
        f"• **{e['name']}**" + (f" — {', '.join(e['series'])}" if e.get("series") else "")
        for e in results
    ]
    lines.append("\nSubscribe with `/subscribe event` or `/subscribe series` — pick from the list.")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


GITHUB_REPO = os.environ.get("GITHUB_REPO")  # owner/repo
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # PAT (contents+PR write) -> bot opens PRs


def _fmt_dt(value) -> str:
    """Format a round date for the embed. Accepts an ISO string OR a date/datetime
    (the generic adapter yields datetimes, the LLM path yields ISO strings)."""
    if not value:
        return "—"
    s = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return s.replace("T", " ")[:16]


def build_event_embed(data: dict, slug: str, src: str) -> discord.Embed:
    """A review embed of the scraped event (truncated to Discord's field limits)."""
    emb = discord.Embed(
        title=(data.get("name") or "(no name)")[:256],
        description=data.get("name_en"),
        url=data.get("source_url") or None,
        color=0xFF5FA2,
    )
    meta = " · ".join(
        x for x in [data.get("kind"), data.get("artist"), ", ".join(data.get("series", []))] if x
    )
    if meta:
        emb.add_field(name="What", value=meta[:1024], inline=False)
    perfs = data.get("performances", [])
    if perfs:
        lines = []
        for p in perfs[:8]:
            loc = " ".join(x for x in [p.get("city"), p.get("venue")] if x)
            # label ("昼公演"/"Day 1") + start time distinguish same-venue shows
            extra = " ".join(
                x for x in [p.get("label"), (f"開演{p['starts']}" if p.get("starts") else "")] if x
            )
            line = f"`{p.get('date', '?')}` {loc}".rstrip()
            if extra:
                line += f" — {extra}"
            lines.append(line)
        if len(perfs) > 8:
            lines.append(f"…and {len(perfs) - 8} more")
        emb.add_field(
            name=f"Performances ({len(perfs)})", value="\n".join(lines)[:1024], inline=False
        )
    # rounds live under performances (with an event-wide top-level list as fallback);
    # flatten + dedupe for the review embed.
    rounds, seen = [], set()
    for r in data.get("rounds", []) + [r for p in perfs for r in p.get("rounds", [])]:
        key = (r.get("name"), r.get("leg"), str(r.get("apply_deadline")))
        if key in seen:
            continue
        seen.add(key)
        rounds.append(r)
    if rounds:
        lines = [
            f"`{_fmt_dt(r.get('apply_deadline'))}` {(r.get('leg') + ': ' if r.get('leg') else '')}"
            f"{r.get('name', '?')}"[:120]
            for r in rounds[:10]
        ]
        if len(rounds) > 10:
            lines.append(f"…and {len(rounds) - 10} more")
        emb.add_field(
            name=f"Lottery rounds ({len(rounds)})", value="\n".join(lines)[:1024], inline=False
        )
    emb.set_footer(text=f"via {src} · events/{slug}.yaml · ⚠ verify dates before confirming")
    return emb


# ---- merge support: fold a new URL's rounds into an existing event ----
# (Some sources announce each lottery round as a separate post, so /add needs to
#  recognise "this is an update to an event I already track" and append to it.)


def _norm_date(v) -> str:
    return (v.isoformat() if hasattr(v, "isoformat") else str(v or ""))[:10]


def _round_key(r: dict) -> str:
    """Dedup identity for a round: apply deadline at minute precision (tz-agnostic —
    everything is JST), else name+leg. Matches both stored ISO strings and freshly
    parsed datetimes, so re-adding the same post is a no-op."""
    v = r.get("apply_deadline")
    if v:
        s = v.isoformat() if hasattr(v, "isoformat") else str(v)
        return "dl:" + s[:16]
    return f"nm:{r.get('name', '')}|{r.get('leg', '')}"


def _loose_eq(a, b) -> bool:
    """Tolerant string match for venue/label: True if either side is blank, or they
    are equal / one contains the other (spaces ignored). So '東京・下北沢シャングリラ'
    matches '下北沢シャングリラ', and 'The story resumes EXTRA' matches
    'LustQueen「The story resumes EXTRA」' — but '昼公演' ≠ '夜公演'."""
    na = (a or "").replace(" ", "").replace("　", "")
    nb = (b or "").replace(" ", "").replace("　", "")
    if not (na and nb):
        return True
    return na == nb or na in nb or nb in na


def _perf_same(a: dict, b: dict) -> bool:
    """Whether two performances are the same show. Requires same date + compatible
    venue. Then distinct iff they have *different* explicit start times (noon vs
    evening). Start times are often announced only later, so when they're missing we
    fall back to the label to tell apart multiple same-day/venue shows."""
    if _norm_date(a.get("date")) != _norm_date(b.get("date")):
        return False
    if not _loose_eq(a.get("venue"), b.get("venue")):
        return False
    sa, sb = a.get("starts") or "", b.get("starts") or ""
    if sa and sb and sa != sb:
        return False
    return _loose_eq(a.get("label"), b.get("label"))


def _merge_perfs(existing: list, new: list) -> tuple[list, int, int]:
    """Merge performances (rounds nested under each). For a same-show match, keep the
    richer record, fill missing fields, and merge its rounds (dedup by deadline);
    otherwise append the new show. Returns (merged, n_new_perfs, n_new_rounds)."""
    merged = [dict(p) for p in existing]
    for p in merged:
        p["rounds"] = [dict(r) for r in p.get("rounds", [])]
    n_perfs = n_rounds = 0
    for np in new:
        i = next((i for i, ep in enumerate(merged) if _perf_same(ep, np)), None)
        if i is None:
            mp = dict(np)
            mp["rounds"] = [dict(r) for r in np.get("rounds", [])]
            merged.append(mp)
            n_perfs += 1
            n_rounds += len(mp["rounds"])
            continue
        ep = merged[i]
        for k, v in np.items():
            if k != "rounds" and v and not ep.get(k):
                ep[k] = v
        have = {_round_key(r) for r in ep.get("rounds", [])}
        for r in np.get("rounds", []):
            if _round_key(r) not in have:
                ep.setdefault("rounds", []).append(dict(r))
                have.add(_round_key(r))
                n_rounds += 1
    return merged, n_perfs, n_rounds


def _perf_dates(ev: dict) -> set[str]:
    return {_norm_date(p.get("date")) for p in ev.get("performances", []) if p.get("date")}


def _perf_date_venues(ev: dict) -> set[tuple]:
    return {
        (_norm_date(p.get("date")), p.get("venue") or "")
        for p in ev.get("performances", [])
        if p.get("date")
    }


def find_matching_event(data: dict, slug: str, events: list[dict]) -> dict | None:
    """The existing event this ingested page most likely refers to, or None.

    Round-announcement posts often have a different title and even different venue
    strings (LLM vs deterministic adapter) from the original, so we match by, in
    order: exact slug, exact name, shared performance date+venue, or — when the
    same artist plays — a shared performance *date* alone (handles venue-string
    drift between posts). A bare date overlap without a matching artist is ignored
    to avoid merging two unrelated events that happen to share a day.
    """
    # Stable cross-source join: an ll-fans tour id matches regardless of name drift.
    lf = data.get("llfans_id")
    if lf:
        for e in events:
            if e.get("llfans_id") == lf:
                return e
    by_id = {e["id"]: e for e in events}
    if slug in by_id:
        return by_id[slug]
    name = (data.get("name") or "").strip()
    if name:
        for e in events:
            if (e.get("name") or "").strip() == name:
                return e

    new_dates, new_dv = _perf_dates(data), _perf_date_venues(data)
    artist = (data.get("artist") or "").strip().lower()
    best, best_score = None, 0
    for e in events:
        dv_ov = len(new_dv & _perf_date_venues(e))
        date_ov = len(new_dates & _perf_dates(e))
        same_artist = bool(artist) and artist == (e.get("artist") or "").strip().lower()
        if dv_ov:  # same date AND venue — conclusive
            score = 100 + dv_ov
        elif date_ov and same_artist:  # same date + same artist — safe despite venue drift
            score = 10 + date_ov
        else:
            score = 0
        if score > best_score:
            best, best_score = e, score
    return best if best_score else None


def merge_event_data(existing: dict, new: dict) -> tuple[dict, int, int]:
    """Fold `new` into a copy of `existing` with rounds nested under performances.
    Existing fields are preserved (the new post is usually sparse); only empty
    top-level scalars are filled in. Returns (merged, n_new_rounds, n_new_perfs)."""
    # Drop derived keys (event_dates/venues and the flat `rounds` mirror); the
    # canonical rounds live under performances.
    merged = {k: v for k, v in existing.items() if k not in ("event_dates", "venues", "rounds")}
    new = nest_rounds(dict(new))  # normalize incoming flat rounds -> nested
    n_p = n_r = 0
    if new.get("performances"):
        merged["performances"], n_p, n_r = _merge_perfs(
            merged.get("performances", []), new["performances"]
        )
    for f in ("name_en", "artist", "kind", "official_url", "eventernote_url", "llfans_id"):
        if not merged.get(f) and new.get(f):
            merged[f] = new[f]
    return merged, n_r, n_p


def _validate_draft(slug: str, yaml_text: str) -> dict:
    """Parse + schema-validate a draft YAML; return the raw dict (raises if invalid)."""
    import yaml as _yaml

    from schema.models import Event

    raw = _yaml.safe_load(yaml_text) or {}
    raw["id"] = slug
    Event.model_validate(raw)
    return raw


async def _confirm_add(slug: str, yaml_text: str) -> str:
    """Validate the draft and commit it straight to main. Returns a status."""
    try:
        _validate_draft(slug, yaml_text)
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Draft failed validation, not saved:\n```{str(exc)[:400]}```"

    if not (GITHUB_REPO and GITHUB_TOKEN):
        return "⚠️ No GITHUB_REPO/GITHUB_TOKEN configured — can't save."

    from . import gh

    try:
        url = await asyncio.to_thread(
            gh.commit_to_main,
            GITHUB_REPO,
            GITHUB_BRANCH,
            GITHUB_TOKEN,
            slug,
            yaml_text,
            f"add/update {slug} via /add",
        )
        return f"✅ Saved to `{GITHUB_BRANCH}` — live in ~1 min: {url}"
    except Exception as exc:  # noqa: BLE001
        log.exception("/add save failed slug=%s", slug)
        return f"⚠️ Save failed: {exc}\nUse the attached YAML to add it manually."


class EditModal(discord.ui.Modal, title="Edit event draft (YAML)"):
    def __init__(self, view: AddConfirmView):
        super().__init__()
        self._view = view
        self.yaml_input = discord.ui.TextInput(
            label=f"events/{view.slug}.yaml"[:45],
            style=discord.TextStyle.paragraph,
            default=view.yaml_text[:4000],
            max_length=4000,
            required=True,
        )
        self.add_item(self.yaml_input)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.yaml_input.value
        try:
            raw = _validate_draft(self._view.slug, text)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(
                f"⚠️ Invalid YAML — edit not applied:\n```{str(exc)[:400]}```", ephemeral=True
            )
            return
        self._view.yaml_text = text
        embed = build_event_embed(raw, self._view.slug, "edited")
        file = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"{self._view.slug}.yaml")
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self._view)


class AddConfirmView(discord.ui.View):
    def __init__(self, author_id: int, slug: str, yaml_text: str):
        super().__init__(timeout=600)  # allow time to edit
        self.author_id = author_id
        self.slug = slug
        self.yaml_text = yaml_text

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the requester can confirm.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm & save", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        msg = await _confirm_add(self.slug, self.yaml_text)
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(content=msg, view=self)
        self.stop()

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.yaml_text) > 4000:
            await interaction.response.send_message(
                "Draft is over 4000 chars — too long for the inline editor; edit it in the PR/file.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(EditModal(self))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled — nothing added.", view=self)
        self.stop()


class MergeConfirmView(discord.ui.View):
    """Shown when /add recognises the URL as an update to an existing event:
    merge the new rounds in, edit first, create a separate new event, or cancel."""

    def __init__(self, author_id: int, slug: str, yaml_text: str, new_slug: str, new_yaml: str):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.slug = slug  # existing event id (merge target) — also used by EditModal
        self.yaml_text = yaml_text  # merged YAML
        self.new_slug = new_slug  # fallback: commit as a brand-new event
        self.new_yaml = new_yaml

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the requester can confirm.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm update", style=discord.ButtonStyle.success, emoji="🔄")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        msg = await _confirm_add(self.slug, self.yaml_text)
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(content=msg, view=self)
        self.stop()

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.yaml_text) > 4000:
            await interaction.response.send_message(
                "Draft is over 4000 chars — too long for the inline editor.", ephemeral=True
            )
            return
        await interaction.response.send_modal(EditModal(self))

    @discord.ui.button(label="Create new instead", style=discord.ButtonStyle.secondary, emoji="🆕")
    async def create_new(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        msg = await _confirm_add(self.new_slug, self.new_yaml)
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(content=msg, view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled — nothing changed.", view=self)
        self.stop()


@tree.command(description="Draft an event from any URL (official, FC, live-house, …)")
@app_commands.describe(
    url="event page URL",
    llm="force LLM (Vertex) extraction",
    event="merge the scraped rounds into this existing event (optional)",
)
async def add(
    interaction: discord.Interaction, url: str, llm: bool = False, event: str | None = None
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    log.info("/add user=%s url=%s force_llm=%s", interaction.user, url, llm)

    # Stream progress into the (deferred) reply. ingest runs in a worker thread, so
    # its progress callback hops back onto the event loop to edit the message.
    loop = asyncio.get_running_loop()

    async def _status(text: str):
        try:
            await interaction.edit_original_response(content=text)
        except Exception:  # noqa: BLE001 - status is best-effort; never crash /add
            log.debug("status edit failed", exc_info=True)

    def progress(text: str):
        asyncio.run_coroutine_threadsafe(_status(text), loop)

    await _status(f"🔎 Fetching {url} …")
    try:  # fetch + parse off the event loop (blocking I/O + optional LLM)
        res = await asyncio.to_thread(ingest_url, url, True, llm, progress)
    except Exception as exc:  # noqa: BLE001
        log.exception("/add failed url=%s", url)
        await interaction.edit_original_response(content=f"⚠️ Couldn't ingest that URL: {exc}")
        return
    log.info(
        "/add done url=%s adapter=%s used_llm=%s rounds=%d",
        url,
        res.adapter,
        res.used_llm,
        len(res.data.get("rounds", [])),
    )

    try:
        data = res.data
        dates = [p["date"] for p in data.get("performances", []) if p.get("date")]
        slug = slugify(data.get("name") or "", dates)
        new_yaml = to_event_yaml(data)
        src = "LLM (Vertex)" if res.used_llm else res.adapter
        events = _events_cache or refresh_events()

        # Decide: merge into an existing event, or create a new one. An explicit
        # `event` arg forces the target; otherwise we try to auto-detect a match.
        if event:
            target = next((e for e in events if e["id"] == event), None)
            if not target:
                await interaction.edit_original_response(
                    content=f"⚠️ Unknown event `{event}` to merge into."
                )
                return
        else:
            target = find_matching_event(data, slug, events)

        if target:
            merged, n_r, n_p = merge_event_data(target, data)
            merged_yaml = to_event_yaml(merged)
            embed = build_event_embed(merged, target["id"], src)
            view = MergeConfirmView(interaction.user.id, target["id"], merged_yaml, slug, new_yaml)
            file = discord.File(
                io.BytesIO(merged_yaml.encode("utf-8")), filename=f"{target['id']}.yaml"
            )
            bits = []
            if n_r:
                bits.append(f"**+{n_r} new round{'s' if n_r != 1 else ''}**")
            if n_p:
                bits.append(f"+{n_p} new performance{'s' if n_p != 1 else ''}")
            change = ", ".join(bits) if bits else "no new rounds or performances"
            await interaction.edit_original_response(
                content=(
                    f"🔄 This looks like an update to **{target['name']}** — {change}. "
                    "Confirm to update it, Edit first, or create a new event."
                )[:1900],
                embed=embed,
                view=view,
                attachments=[file],
            )
            return

        embed = build_event_embed(data, slug, src)
        view = AddConfirmView(interaction.user.id, slug, new_yaml)
        file = discord.File(io.BytesIO(new_yaml.encode("utf-8")), filename=f"{slug}.yaml")
        await interaction.edit_original_response(
            content=f"✅ Parsed via **{src}** — review, then **Confirm** to save (or Edit first):",
            embed=embed,
            view=view,
            attachments=[file],
        )
    except Exception as exc:  # noqa: BLE001 - never leave the spinner hanging
        log.exception("/add render failed url=%s", url)
        await interaction.edit_original_response(
            content=f"⚠️ Parsed the page but couldn't build the preview: {exc}"
        )


@add.autocomplete("event")
async def _add_event_ac(interaction: discord.Interaction, current: str):
    return await _event_ac(interaction, current)


@tree.command(description="(Admin) Upcoming LL events on LLFans we don't track yet")
@app_commands.default_permissions(manage_guild=True)
async def discover(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    from scrape import llfans

    today = datetime.now(JST).date().isoformat()
    try:
        tours = await asyncio.to_thread(llfans.upcoming_tours, today)
    except Exception as exc:  # noqa: BLE001
        log.exception("/discover failed")
        await interaction.followup.send(f"⚠️ Couldn't reach LLFans: {exc}", ephemeral=True)
        return

    def _squash(s):
        return (s or "").replace(" ", "").replace("　", "")

    events = _events_cache or refresh_events()
    have_lf = {e.get("llfans_id") for e in events if e.get("llfans_id")}
    have_nm = {_squash(e.get("name")) for e in events}
    new = [t for t in tours if str(t["id"]) not in have_lf and _squash(t["name"]) not in have_nm]
    if not new:
        await interaction.followup.send(
            f"✅ All {len(tours)} upcoming LL events on LLFans are already tracked.",
            ephemeral=True,
        )
        return
    lines = [f"**{len(new)} untracked upcoming events** (of {len(tours)} on LLFans):"]
    for t in new[:15]:
        span = t["startsOn"] + (f"→{t['endsOn']}" if t["endsOn"] != t["startsOn"] else "")
        lines.append(f"• {span} — {(t['name'] or '')[:46]}\n  `/add` <{llfans.event_url(t['id'])}>")
    if len(new) > 15:
        lines.append(
            f"…and {len(new) - 15} more (run `python -m scrape.discover` for the full list)"
        )
    await interaction.followup.send("\n".join(lines)[:1950], ephemeral=True)


async def _delete_event(slug: str) -> str:
    """Delete events/<slug>.yaml from main. Returns a status."""
    if not (GITHUB_REPO and GITHUB_TOKEN):
        return "⚠️ No GITHUB_REPO/GITHUB_TOKEN configured — can't delete."
    from . import gh

    try:
        url = await asyncio.to_thread(
            gh.delete_from_main,
            GITHUB_REPO,
            GITHUB_BRANCH,
            GITHUB_TOKEN,
            slug,
            f"delete {slug} via /delete",
        )
        return f"🗑 Deleted `{slug}` from `{GITHUB_BRANCH}` — gone in ~1 min: {url}"
    except Exception as exc:  # noqa: BLE001
        log.exception("/delete failed slug=%s", slug)
        return f"⚠️ Delete failed: {exc}"


class DeleteConfirmView(discord.ui.View):
    def __init__(self, author_id: int, slug: str):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.slug = slug

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the requester can confirm.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        msg = await _delete_event(self.slug)
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(content=msg, view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled — nothing deleted.", view=self)
        self.stop()


@tree.command(description="Delete a tracked event (admin)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(event="event to delete")
async def delete(interaction: discord.Interaction, event: str):
    ev = next((e for e in (_events_cache or refresh_events()) if e["id"] == event), None)
    name = ev["name"] if ev else event
    await interaction.response.send_message(
        f"Delete **{name}** (`{event}`)? This removes it from the site.",
        view=DeleteConfirmView(interaction.user.id, event),
        ephemeral=True,
    )


@delete.autocomplete("event")
async def _delete_ac(interaction: discord.Interaction, current: str):
    cur = current.lower()
    evs = _events_cache or refresh_events()
    matches = [e for e in evs if cur in e["id"].lower() or cur in e["name"].lower()]
    return [app_commands.Choice(name=e["name"][:100], value=e["id"]) for e in matches[:25]]


subscribe = app_commands.Group(name="subscribe", description="Subscribe to events or series")
unsubscribe = app_commands.Group(name="unsubscribe", description="Remove a subscription")
tree.add_command(subscribe)
tree.add_command(unsubscribe)


@subscribe.command(name="event", description="Get reminders for one event")
async def subscribe_event(interaction: discord.Interaction, event: str):
    ev = next((e for e in (_events_cache or refresh_events()) if e["id"] == event), None)
    if not ev:
        await interaction.response.send_message(f"Unknown event `{event}`.", ephemeral=True)
        return
    ok = db.add_subscription(str(interaction.user.id), "event", event)
    db.mark_event_notified(str(interaction.user.id), event)  # don't "new-event" ping for it
    msg = f"✅ Subscribed to **{ev['name']}**." if ok else "Already subscribed."
    await interaction.response.send_message(msg, ephemeral=True)


@subscribe_event.autocomplete("event")
async def _event_ac(interaction: discord.Interaction, current: str):
    """Pick an event from a list (so the id can't be typo'd)."""
    cur = current.lower()
    evs = _events_cache or refresh_events()
    matches = [e for e in evs if cur in e["id"].lower() or cur in e["name"].lower()]
    return [app_commands.Choice(name=e["name"][:100], value=e["id"]) for e in matches[:25]]


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
async def unsubscribe_event(interaction: discord.Interaction, event: str):
    ok = db.remove_subscription(str(interaction.user.id), "event", event)
    await interaction.response.send_message(
        "✅ Removed." if ok else "You weren't subscribed.", ephemeral=True
    )


@unsubscribe_event.autocomplete("event")
async def _unsub_event_ac(interaction: discord.Interaction, current: str):
    """Only suggest events the user is actually subscribed to."""
    cur = current.lower()
    names = {e["id"]: e["name"] for e in (_events_cache or refresh_events())}
    subs = [
        s["target"] for s in db.list_subscriptions(str(interaction.user.id)) if s["kind"] == "event"
    ]
    out = []
    for sid in subs:
        name = names.get(sid, sid)
        if cur in name.lower() or cur in sid.lower():
            out.append(app_commands.Choice(name=name[:100], value=sid))
    return out[:25]


@unsubscribe.command(name="series", description="Stop reminders for a series")
async def unsubscribe_series(interaction: discord.Interaction, series: str):
    ok = db.remove_subscription(str(interaction.user.id), "series", series)
    await interaction.response.send_message(
        "✅ Removed." if ok else "You weren't subscribed.", ephemeral=True
    )


@unsubscribe_series.autocomplete("series")
async def _unsub_series_ac(interaction: discord.Interaction, current: str):
    cur = current.lower()
    subs = [
        s["target"]
        for s in db.list_subscriptions(str(interaction.user.id))
        if s["kind"] == "series"
    ]
    return [app_commands.Choice(name=s, value=s) for s in subs if cur in s.lower()][:25]


@tree.command(description="List your subscriptions")
async def subscriptions(interaction: discord.Interaction):
    subs = db.list_subscriptions(str(interaction.user.id))
    if not subs:
        await interaction.response.send_message(
            "No subscriptions yet. Try `/search`.", ephemeral=True
        )
        return
    events = sorted(event_name(s["target"]) for s in subs if s["kind"] == "event")
    series = sorted(s["target"] for s in subs if s["kind"] == "series")
    blocks = []
    if events:
        blocks.append("**Events**\n" + "\n".join(f"• {n}" for n in events))
    if series:
        blocks.append("**Series**\n" + "\n".join(f"• {n}" for n in series))
    await interaction.response.send_message(
        "Your subscriptions:\n" + "\n\n".join(blocks), ephemeral=True
    )


@tree.command(description="Your upcoming application dates")
async def upcoming(interaction: discord.Interaction):
    subs = db.list_subscriptions(str(interaction.user.id))
    now = datetime.now(JST)
    from .reminders import date_tag, occurrences

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
        f"{date_tag(dt)} {discord_ts(t, 'f')} ({discord_ts(t, 'R')}) — "
        f"**{ev['name']}** {rnd['name']}{_apply_link(rnd, dt)}"
        for t, ev, rnd, dt in rows[:15]
    ]
    emb = discord.Embed(
        title="Your upcoming application dates",
        description="\n".join(lines)[:4096],
        color=0xFF5FA2,
    )
    await interaction.response.send_message(embed=emb, ephemeral=True)


@tree.command(description="Upcoming application dates for one event")
@app_commands.describe(event="event to look up")
async def deadlines(interaction: discord.Interaction, event: str):
    from .reminders import date_tag, occurrences

    ev = next((e for e in (_events_cache or refresh_events()) if e["id"] == event), None)
    if not ev:
        await interaction.response.send_message(f"Unknown event `{event}`.", ephemeral=True)
        return
    now = datetime.now(JST)
    rows = sorted(
        ((target, rnd, dtype) for _, rnd, dtype, target in occurrences([ev]) if target > now),
        key=lambda r: r[0],
    )
    if not rows:
        await interaction.response.send_message(
            f"No upcoming application dates for **{ev['name']}**.", ephemeral=True
        )
        return
    lines = []
    for target, rnd, dtype in rows:
        leg = f" · {rnd['leg']}" if rnd.get("leg") else ""
        lines.append(
            f"{date_tag(dtype)} {discord_ts(target, 'f')} ({discord_ts(target, 'R')}) — "
            f"{rnd['name']}{leg}{_apply_link(rnd, dtype)}"
        )
    # Embed so the [apply](url) masked links and the title link render as clickable.
    emb = discord.Embed(
        title=f"{ev['name']} — upcoming dates"[:256],
        url=event_official_link(ev),
        description="\n".join(lines)[:4096],
        color=0xFF5FA2,
    )
    await interaction.response.send_message(embed=emb, ephemeral=True)


@deadlines.autocomplete("event")
async def _deadlines_ac(interaction: discord.Interaction, current: str):
    return await _event_ac(interaction, current)


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
