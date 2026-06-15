# LL Lottery Tracker

A tracker for **Love Live! & seiyuu events** and their **lottery (klottery / 抽選 / 先行)
application rounds**. Lottery rounds are announced in scattered places — individual
artist X accounts, fanclub sites, official/ticket pages — each with its own apply-open
and apply-deadline. This collects them in one place:

- a **static website** (eventernote-style) — upcoming deadlines, searchable catalog,
  per-event pages, and a calendar;
- a **Discord bot** (milestones 4–5) — search events, subscribe to specific events or
  whole series, and get reminded before each deadline.

## How it works

```
events/*.yaml  --validate-->  data/events.json  --render-->  site/dist/  --> GitHub Pages
   (source of truth)             (compiled API)                 (static)        \--> Discord bot reads
```

Each event is one hand-curated YAML file under `events/` (the **source of truth**).
An **event is a whole tour** (à la [the-sorter](https://github.com/hamproductions/the-sorter)'s
`tourName`): it holds a list of **performances** (each show's date/venue/times) and the
lottery **rounds**, which can be tagged by `leg` when they only apply to part of the tour.
`scrape/` helpers (milestone 2) pre-fill drafts from a URL. Datetimes are stored in
**JST** (`+09:00`); the site can toggle to your local timezone.

## Quick start

Dependencies are managed with [uv](https://docs.astral.sh/uv/) (`pyproject.toml` +
`uv.lock`). `uv run` provisions the environment automatically — no manual venv.

```bash
uv run python -m build.build_site         # validates YAML, writes data/events.json + site/dist
uv run python -m http.server --directory site/dist   # then open http://localhost:8000
```

## Layout

| Path | What |
|------|------|
| `events/` | One YAML per event — **edit these** |
| `schema/models.py` | pydantic models (`Event` = tour, `Performance`, `Round`) + JST normalisation |
| `build/build_site.py` | Validate → `data/events.json` → render `site/dist/` |
| `build/templates/`, `build/static/` | Jinja templates + CSS/JS |
| `data/events.json` | Derived artifact compiled from `events/*.yaml` (gitignored; rebuilt at deploy, fetched-by-URL by the bot, compiled-on-demand in dev) |
| `scrape/` | URL → draft YAML helpers (milestone 2) |
| `bot/` | Discord bot (milestones 4–5) |
| `.github/workflows/deploy.yml` | CI: build + deploy to GitHub Pages on push |

## Adding an event

1. Create `events/<slug>.yaml` (copy an existing one) — or run a scrape helper (milestone 2).
2. An event is a **tour**: list each show under `performances:` (date / venue / city / times),
   then the lottery `rounds:` (`apply_open`, `apply_deadline`, `results_date`,
   `payment_deadline`). Tag a round's `leg:` if it only applies to part of the tour.
3. `uv run python -m build.build_site` to validate, then commit. CI rebuilds and deploys.

See `events/2026-liella-7th.yaml` (multi-leg tour, per-leg lotteries) for the full schema.

### Scrape helpers (drafts only — run locally)

```bash
uv run python -m scrape.cli https://ramen.events/some-event/   # scrape -> draft
uv run python -m scrape.cli --text "FC先行 2026年6月25日 23:59まで"  # paste X-post text
uv run python add_event.py "New Event Name"                    # blank draft
```

Scrapers pull event metadata (name/date/venue/cast); **lottery rounds stay
manual** since they're rarely on the source page. Each writes `events/<slug>.yaml`
for you to finish and commit. Eventernote parsing is good; ticket/official pages
and X posts are best-effort. The scraper never runs on a server — only locally.

**LLFans base-event seeding:** `/add https://ll-fans.jp/data/event/<id>` pulls a
tour's clean base metadata — name, series, every show (date/venue/open+start
times), and the **official URL** — from the [ll-fans.jp](https://ll-fans.jp)
GraphQL API (`scrape/llfans.py`). It carries **no lottery rounds** (LLFans doesn't
track them), so the role split is: LLFans seeds the base event + performances, and
the official/FC page (via `/add` or the watcher) supplies the deadlines. The merge
joins them — and stamps `llfans_id` as a stable cross-source key so re-syncs and
official-page matches line up exactly regardless of name drift.

**Discovery:** `/discover` (admin) — or `python -m scrape.discover` — lists every
upcoming LL tour on LLFans, flags which we already track (by `llfans_id` or name),
and prints an `/add` URL for each new one, so nothing slips through.

**Past archive:** `python -m scrape.backfill` pulls all *past* LL tours from LLFans
into `data/past_events.json` (name, dates, series, links — no rounds). The site
renders them as a separate, searchable **Past** tab that links out to LLFans /
official. It's archive-only: the bot and `events/` are untouched.

**Dispatch policy:** domain-tuned adapters (ll-fans.jp, lovelive-anime.jp,
eventernote, x) run deterministically and only fall back to the LLM if they find
nothing.
**Arbitrary pages go LLM-first** — the generic 【label】 parser mangles real ticket
pages (per-day rounds, per-day apply URLs), so it's only used when the LLM is
unavailable. This keeps the daily watcher cheap on official pages while making
`/add <random FC/ticket URL>` reliable.

### Web add/edit form

The site's **+ Add** page builds event YAML in the browser from structured fields,
validates it, and **💾 Save**s it straight to `main` (auto-deploys in ~1 min). Each
event page has a **✎ Edit event** button that opens the same form prefilled (slug
locked) from existing data — so add and edit are the same quick flow, no PR.

Saving goes through a small GCP **Cloud Function** (`functions/commit/`): the static
site can't hold a write token, so the function holds the PAT server-side and is
guarded by an admin secret; the browser sends `POST {slug, yaml}` + the secret. The
function URL is **baked into the build** (`EDIT_API_URL`, public — not a secret), so
the only thing to enter is the **Admin secret**, once, in the form's Config (stored
in `localStorage`); the Config block auto-hides after that. Deploy/update the
function with:

```bash
gcloud functions deploy ll-commit --gen2 --region us-central1 --runtime python312 \
  --source functions/commit --entry-point commit --trigger-http --allow-unauthenticated \
  --set-env-vars GITHUB_REPO=<owner/repo>,GITHUB_BRANCH=main,\
ALLOW_ORIGIN=https://<owner>.github.io,GITHUB_TOKEN=<pat>,ADMIN_SECRET=<secret>
```

## Discord bot

A `discord.py` bot lets you search events, subscribe to specific events or whole
series, and get reminded before each tracked date (DM and/or a server channel).

```bash
cp .env.example .env          # fill in DISCORD_TOKEN, EVENTS_SOURCE, SITE_URL
uv run --extra bot python -m bot.main
```

### Commands

All replies are **ephemeral** (only you see them). Event/series arguments use
**autocomplete** — start typing a name and pick from the list, so ids can't be
typo'd. Dates render as Discord **dynamic timestamps**, shown in each viewer's
own timezone (absolute + relative, e.g. "in 3 days"). Each lottery date carries
an emoji: 🟢 opens · 🔴 deadline · 🎯 results · 💰 payment. In `/upcoming` and
`/deadlines`, rounds with an application URL show a clickable **[apply]** link.

| Command | What it does |
|---------|--------------|
| `/search <query>` | Find tracked events by name, series, venue or performer. |
| `/subscribe event <event>` | Get reminders for one event's lottery dates. |
| `/subscribe series <series>` | Get reminders for **every** event in a series (incl. future ones). |
| `/unsubscribe event <event>` | Stop reminders for one event (autocompletes from *your* subs). |
| `/unsubscribe series <series>` | Stop reminders for a series. |
| `/subscriptions` | List what you follow, grouped into **Events** / **Series**. |
| `/upcoming` | Your next application dates **across all your subscriptions**, soonest-first (≤15). |
| `/deadlines <event>` | Upcoming dates for **one specific event** (subscribed or not); links to its official page. |
| `/settings [lead_times] [dm]` | View or change reminder lead times (e.g. `3d,1d,2h`) and DM on/off. |
| `/add <url> [llm]` | Draft an event from any URL — see below. |
| `/discover` | **(Admin)** Upcoming LL events on LLFans we don't track yet, each with an `/add` URL. |
| `/setchannel` | **(Admin)** Also post reminders to the current channel. |
| `/delete <event>` | **(Admin)** Remove an event from the site (confirm prompt first). |
| `/testreminder` | **(Admin)** DM yourself a sample reminder to verify delivery. |

Admin commands are gated by Discord's `manage_guild` permission.

**`/add <url>`** ingests any event page (official / FC / live-house) via the
hybrid pipeline and shows a **review embed** (name, performances, lottery rounds)
with **Confirm / Edit / Cancel** buttons. On Confirm the draft is
schema-validated and the bot **commits it straight to `main`** (needs
`GITHUB_TOKEN`). Edit opens a modal to tweak the YAML first; the full YAML is
always attached. Pass `llm:true` to force Vertex extraction. While it works the
reply **streams live status** (fetching → which adapter → AI fallback if a
deterministic adapter finds nothing), so you can see whether the slow LLM path
kicked in. Heavy work runs off the event loop (`asyncio.to_thread`); needs the
`llm`/`bot` extras + GCP creds for the LLM fallback.

**Updating an existing event (new-round posts).** Some sources announce each
lottery round as a *separate* post (e.g. round 1 and round 2 at different URLs).
`/add` a later URL and it **recognises the existing event** (by slug, name, or
shared performances) and switches to a **merge review** — "🔄 update to **X** —
+1 new round" — with **Confirm update / Edit / Create new instead / Cancel**.
The merge appends only genuinely new rounds/performances (deduped by apply
deadline), preserving every existing field, so re-adding the same post is a
no-op. Pass `event:<existing>` to force the target when auto-detection is unsure.

**Reminders:** the scheduler checks every `CHECK_INTERVAL_MIN` minutes and fires
on apply-open, deadline, results, and payment dates. Default lead times are 3d /
1d / 2h before each; a late subscribe gets one message, not a burst (dedup +
suppression via the `sent_reminders` table). Subscriptions live in SQLite, keyed
by Discord user id (multi-user ready).

### Deploy (Docker on GCP)

The bot ships as a container (`Dockerfile`) with a volume for SQLite
(`docker-compose.yml`). It reads config from `.env` and points `EVENTS_SOURCE` at
the published `events.json`, so it's decoupled from the repo.

**Create the Discord app** (Developer Portal → New Application → Bot → copy token;
invite with `applications.commands` + `bot` scopes), then put the token in `.env`.

**Recommended: a small Compute Engine VM** (durable disk for SQLite + automatic
Vertex credentials from the VM's service account — no key file for `/add`'s LLM):

```bash
# one-time: a VM whose service account can call Vertex
gcloud compute instances create ll-bot \
  --machine-type e2-small --boot-disk-size 20GB \
  --scopes cloud-platform                       # ADC -> Vertex AI
gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
  --member "serviceAccount:$(gcloud compute instances describe ll-bot \
     --format='value(serviceAccounts[0].email)')" \
  --role roles/aiplatform.user

# on the VM: install Docker, then
git clone https://github.com/mting314/event-tracker && cd event-tracker
cp .env.example .env && nano .env               # DISCORD_TOKEN, GOOGLE_CLOUD_PROJECT, …
docker compose up -d --build
docker compose logs -f bot                       # "Logged in as …"
```

The container's `DB_PATH=/data/tracker.db` is backed by the `tracker-data` volume,
so subscriptions and sent-reminder state survive restarts/redeploys. On a GCE VM the
bot picks up Vertex credentials automatically; elsewhere set
`GOOGLE_APPLICATION_CREDENTIALS` to a mounted service-account key (or drop the `llm`
extra — `/add` still works via the deterministic adapters).

Slash commands: set **`DISCORD_GUILD_ID`** to a server id for **instant** per-guild
command sync (great for testing); leave it unset for global commands (≈1h to appear).

> Cloud Run isn't ideal here: a Discord **gateway** bot needs an always-on outbound
> connection and durable local disk, which a request-driven, stateless service fights.

## Development

```bash
uv run --extra dev --extra bot python -m pytest    # tests (offline): parsers, bot logic, /add command
uv run --extra dev ruff check .                    # lint
uv run --extra dev ruff format .                   # format
uv run --extra dev pre-commit install              # enable the git hook (ruff + format + yaml checks)
```

Lint, format, and tests also run in CI (`.github/workflows/lint.yml`) on every push/PR.
Config lives in `pyproject.toml` (`[tool.ruff]`) and `.pre-commit-config.yaml`.

## Watcher (official-page polling)

A scheduled job polls the watchlist in `sources.yaml`, parses lottery rounds, and
**diffs against `events/*.yaml`**, reporting new events / new rounds / changed
deadlines. It never edits `events/` — it writes draft snippets to `drafts/` and
(in CI) opens a PR you review.

Each source picks its parser with **`adapter:`** — `auto` (default: domain dispatch +
LLM fallback), `official`, `generic`, `eventernote`, or `llm` — so the watcher can
monitor **any** event source (official LL pages, artist/FC pages, …), not just
`lovelive-anime.jp`:

```yaml
sources:
  - id: 2026-liella-7th
    url: https://www.lovelive-anime.jp/.../7thlive
    adapter: official
  - id: 2026-lustqueen-sept
    url: https://lustqueen.info/news/detail/81252
    adapter: generic
```

```bash
uv run python -m scrape.watch            # report diffs
uv run python -m scrape.watch --write    # also write drafts/<id>.rounds.yaml
```

- **Auto-watch:** beyond `sources.yaml`, the watcher also re-scans every tracked
  event that has an `official_url`/`source_url`, isn't already listed above, and
  isn't finished (any performance or round date today-or-later). So events added
  via the bot's `/add` are monitored for new rounds **with no edit to
  `sources.yaml`**; past events drop off automatically to bound the daily cost.
- Handles both official page layouts (`【申込受付期間】…` and `■受付期間：…` / `★申込対象：＜…公演＞`).
- Diffs **by apply deadline** (the stable fact), so it isn't fooled by JP↔EN name/leg
  differences between the official page and our data — it surfaces genuine changes.
  (It already caught a wrong Fan Disc deadline in our ramen-sourced seed.)
- Runs daily via `.github/workflows/watch.yml` (`peter-evans/create-pull-request`).

### Discovering new events (ramen.events as index)

ramen.events lists nearly every LL event and links its official page, so it's the
discovery index. `scrape/ramen.py` crawls its sitemap, extracts the official URLs,
and flags **untracked events that already have upcoming rounds** — i.e. what to add.

```bash
uv run python -m scrape.ramen                         # all official URLs (✓ = tracked)
uv run python -m scrape.ramen --candidates            # untracked events w/ upcoming rounds
uv run python -m scrape.ramen --candidates --today 2026-06-10
```

Best-quality flow for a new event: discover the official URL here → pull the
authoritative **rounds** from the official page (`scrape/official.py`) → fill
**performances / cast / series** from the ramen post → save `events/<slug>.yaml`.

## Ingesting any event (not just Love Live)

The model is generic (`Event → performances → rounds`; `series` is just tags, plus
`artist` / `kind` / `source_url`), so it holds any event. Ingestion is **hybrid,
dispatched by domain** (`scrape/cli.py`):

| Source | Adapter |
|--------|---------|
| `lovelive-anime.jp` | `official.py` (authoritative) |
| `eventernote.com` | `eventernote.py` |
| `x.com` / paste | `x_post.py` — reads the post via the embed API, then **follows the event link inside it** |
| **anything else** (artist/FC/live-house pages) | `generic.py` — generic `【label】` parser |
| **fallback** when the above find nothing | `llm.py` — Vertex AI (Gemini) extractor |

```bash
uv run python add_event.py --url "https://lustqueen.info/news/detail/81252"   # any event → draft YAML
uv run python add_event.py --url "<prose-heavy page>"                          # auto-falls back to LLM
uv run python add_event.py --url "<page>" --llm        # force LLM   |   --no-llm to disable fallback
```

The generic parser reads the common JP FC layout (`【公演日程】/【会場】/受付期間 ※抽選※`)
into performances + lottery windows (verified on LustQueen). When a deterministic
adapter returns nothing, ingestion falls back to the **LLM extractor**.

**X posts as a trigger.** An LL ticket post on X usually carries only a teaser plus
a link to the real event page. `x_post.py` reads the post through X's public embed
API (`cdn.syndication.twimg.com`, no auth) to recover the post text *and* its
expanded links; for long-form **note tweets** (whose link card the embed API hides)
it falls back to a logged-out GraphQL read (guest token + the web app's public
bearer — no dev account). `ingest.py` then **follows the most promising link** (known
event/ticket hosts first) back through the same dispatcher and merges that richer
result — so `/add <x-link>` works even when the details live behind the link, not in
the post. The post also supplies the **show dates** (year-less `6/13-14` lines are
resolved using the tweet's own year, while sale/lottery dates are skipped), so the
linked ticket page's lottery rounds get attached to real performances. Shown in the
embed footer as e.g. `x→official` or `x→llm`.

> The GraphQL fallback is best-effort and brittle — if X rotates the public bearer
> or the query id it 4xx's and we degrade to the embed API (or the LLM/paste path).

### LLM fallback (Pydantic AI — provider-swappable)

`scrape/llm.py` uses **Pydantic AI**, so the model is a one-string swap and the
output is validated against pydantic models (no hand-written schema). It's grounded
strictly on the page text and told to **quote the source line for every date** (kept
in each round's `notes`) so you can verify; the draft is still human-reviewed.

Default is **Vertex AI (Gemini)** via Application Default Credentials (your GCP setup):

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=your-project     # GOOGLE_CLOUD_LOCATION default us-central1
uv sync --extra llm                          # installs pydantic-ai-slim[google]
```

Swap providers with `LLM_MODEL` (no code change):

| `LLM_MODEL` | Backend | Needs |
|-------------|---------|-------|
| `gemini-2.5-flash` *(default, bare = Vertex)* | Vertex AI | GCP ADC |
| `google-cloud:gemini-2.0-flash` | Vertex AI (cheaper) | GCP ADC |
| `anthropic:claude-sonnet-4-5` | Anthropic | `ANTHROPIC_API_KEY` + `uv sync --extra llm` w/ `pydantic-ai-slim[anthropic]` |
| `openai:gpt-4o` | OpenAI | `OPENAI_API_KEY` + `pydantic-ai-slim[openai]` |

## Roadmap

- **Milestone 8 — hybrid ingestion: ✅ complete.** (a) LLM fallback via Vertex AI
  (`scrape/llm.py`); (b) `/add <url>` Discord command (`bot/main.py`); (c) per-source
  `adapter:` so the watcher polls non-LL sources (`scrape/watch.py`); (d) `artist`/`kind`
  surfaced on the catalog (badge + filter), event pages, and the Add form.

- **Milestone 6 — Watcher: ✅ official-page polling (above).** Extension: also parse
  the **ramen.events RSS** as an English secondary source.

- **Milestone 7 — X (Twitter) filtered-search feed (deferred):** add official LL X accounts
  as an *earliest-signal* source. Use the **filtered recent-search** endpoint
  (`from:acct (抽選 OR 先行 OR チケット OR 受付 OR 申込)`) so you pay only for relevant tweets.
  - X API is now **usage-based credits**, not a flat subscription: **Posts: Read = $0.005
    per tweet returned** (User: Read $0.010 if expanding author). Owned-read $0.001 only
    applies to your *own* account's data, not third-party accounts.
  - Napkin cost: filtered search ≈ **~$0.20–1/month** (~40 relevant tweets × $0.005) vs.
    full-timeline polling ≈ $5–22/mo. Likely a small upfront credit purchase / recharge floor.
  - Needs a dev account + app + bearer token (a CI/host secret). Recent-search only covers
    the last 7 days, so poll at least weekly. Twitter is the *trigger*; the structured data
    still comes from the linked official page (so it feeds the same parser).
  - **Click-through is now implemented** for the manual `/add <x-link>` path: `x_post.py`
    reads the post (embed API) and `ingest.py` follows the linked event page automatically.
    Only the *automated filtered-search feed* above remains deferred.
