"""LLM fallback extractor — built on Pydantic AI (provider-swappable).

The hybrid dispatcher falls back here when the deterministic adapters can't
structure a page. Pydantic AI gives us:
  - structured output validated against pydantic models (no hand-written schema), and
  - one-line provider swap via ``LLM_MODEL``.

Default is Vertex AI (Gemini) using Application Default Credentials, matching the
project's GCP setup. Swap providers by setting ``LLM_MODEL``:
    gemini-2.5-pro                 -> Vertex (default; bare name = Vertex)
    google-cloud:gemini-2.0-flash  -> Vertex, cheaper model
    anthropic:claude-sonnet-4-5    -> Anthropic   (needs ANTHROPIC_API_KEY + [anthropic] extra)
    openai:gpt-4o                  -> OpenAI      (needs OPENAI_API_KEY + [openai] extra)

Env: GOOGLE_CLOUD_PROJECT (Vertex), GOOGLE_CLOUD_LOCATION (default us-central1).
ADC: `gcloud auth application-default login`. The model/agent is built lazily, so
this module imports fine without the SDK or credentials (tests inject an agent).
"""

from __future__ import annotations

import logging
import os
import re
import time

from bs4 import BeautifulSoup
from pydantic import BaseModel

from schema.models import KINDS

from .generic import HEADERS
from .glossary import kind_block, prompt_block

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"  # ~2x faster than -pro for this extraction
MAX_CHARS = 12000
REQUEST_TIMEOUT = 90  # seconds — fail fast instead of hanging if Vertex stalls

SYSTEM = (
    "You extract structured event + ticket-lottery data from a web page for a Love "
    "Live / idol / band event tracker. RULES:\n"
    "- Use ONLY information present in the provided page text. Never invent or infer "
    "dates, venues, or names not written there. Omit fields you cannot find.\n"
    "- All datetimes are Japan Standard Time. Output them as naive ISO "
    "'YYYY-MM-DDTHH:MM:SS'. Dates as 'YYYY-MM-DD'.\n"
    "- 'performances' are individual shows (date + venue + open/start times).\n"
    "- Rounds are ticket application windows (抽選 lottery / 先行 presale / 一般 general "
    "/ fanclub / 受付期間). Map 受付期間->apply_open/apply_deadline, 当落発表/抽選結果発表->"
    "results_date, 入金期間->payment_deadline.\n"
    "- Attach each round to the performance(s) it applies to via that performance's "
    "'rounds'. If a round covers several shows (e.g. a whole leg), REPEAT it under "
    "each. Pages like 'DAY1: …受付URL… / DAY2: …' have a different round per day — "
    "attach each to its day. Use the event-level 'rounds' ONLY for a round you truly "
    "cannot tie to specific shows.\n"
    "- For each round, set source_quote to the exact source line(s) the dates came "
    "from, so a human can verify.\n"
    "- OMIT any round you cannot assign at least one date to (apply_open / "
    "apply_deadline / results_date / payment_deadline) — a dateless round is useless.\n"
    + kind_block(KINDS)
    + "\n"
    + prompt_block()
)


# --- extraction schema (LLM-facing): loose, no id, string datetimes ---
class ExtractedRound(BaseModel):
    name: str
    name_en: str | None = None
    type: str | None = None
    leg: str | None = None
    apply_open: str | None = None
    apply_deadline: str | None = None
    results_date: str | None = None
    payment_deadline: str | None = None
    apply_url: str | None = None
    source_quote: str | None = None


class ExtractedPerformance(BaseModel):
    date: str
    city: str | None = None
    label: str | None = None
    venue: str | None = None
    venue_address: str | None = None
    doors: str | None = None
    starts: str | None = None
    rounds: list[ExtractedRound] = []  # lottery rounds for this specific show


class ExtractedEvent(BaseModel):
    name: str
    name_en: str | None = None
    artist: str | None = None
    kind: str | None = None
    series: list[str] = []
    categories: list[str] = []
    performers: list[str] = []
    performances: list[ExtractedPerformance] = []
    rounds: list[ExtractedRound] = []  # event-wide rounds not tied to one show
    notes: str | None = None


def page_text(html: str) -> tuple[str, str]:
    """Return (title, cleaned visible text) trimmed for the prompt."""
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:title")
    title = (og.get("content") if og else None) or (
        soup.title.get_text(strip=True) if soup.title else ""
    )
    for t in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        t.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]
    return title.strip(), "\n".join(lines)[:MAX_CHARS]


def build_prompt(title: str, text: str, url: str | None) -> str:
    return (
        f"Source URL: {url or '(none)'}\nPage title: {title}\n\n"
        f"PAGE TEXT:\n{text}\n\n"
        "Extract the event into the schema. Only use what's written above."
    )


_ROUND_DATE_FIELDS = ("apply_open", "apply_deadline", "results_date", "payment_deadline")


def _fold_quote(r: dict) -> None:
    """Fold a round's source_quote into notes (in place), so it's verifiable in YAML."""
    q = r.pop("source_quote", None)
    if q:
        r["notes"] = (r.get("notes") + " | " if r.get("notes") else "") + f"src: {q}"


def _clean_rounds(rounds: list[dict]) -> list[dict]:
    """Drop rounds with no date (the schema requires one; a dateless round can't be
    tracked) and fold each kept round's source_quote into notes."""
    kept = [r for r in rounds if any(r.get(f) for f in _ROUND_DATE_FIELDS)]
    for r in kept:
        _fold_quote(r)
    return kept


def _to_event_dict(ev: ExtractedEvent, url: str | None) -> dict:
    """Map the validated ExtractedEvent into our ingest dict shape (rounds nested)."""
    data = ev.model_dump(exclude_none=True, exclude_defaults=True)
    for p in data.get("performances", []):
        if p.get("date"):
            p["date"] = re.split(r"[T ]", str(p["date"]))[0]  # date-only
        p["rounds"] = _clean_rounds(p.get("rounds", []))
    if data.get("rounds"):  # event-wide rounds (nested into perfs downstream)
        data["rounds"] = _clean_rounds(data["rounds"])
    if data.get("kind"):  # normalise to the controlled vocab's casing
        data["kind"] = str(data["kind"]).strip().lower()
    data.setdefault("kind", "concert")
    data["source_url"] = url
    return data


def _model():
    """Resolve LLM_MODEL. Bare name or google-cloud:/google-vertex: -> Vertex."""
    spec = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    provider = spec.split(":", 1)[0] if ":" in spec else ""
    if provider in ("", "google-cloud", "google-vertex", "vertex"):
        from pydantic_ai.models.google import GoogleModel

        name = spec.split(":", 1)[1] if ":" in spec else spec
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError("set GOOGLE_CLOUD_PROJECT (+ gcloud auth application-default login)")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        try:  # forward-compatible: GoogleCloudProvider replaces GoogleProvider in v2
            from pydantic_ai.providers.google import GoogleCloudProvider as _Prov

            prov = _Prov(project=project, location=location)
        except ImportError:
            import warnings

            from pydantic_ai.providers.google import GoogleProvider as _Prov

            with warnings.catch_warnings():  # the vertexai= kwarg warns pre-v2
                warnings.simplefilter("ignore")
                prov = _Prov(vertexai=True, project=project, location=location)
        return GoogleModel(name, provider=prov)
    return spec  # anthropic:/openai:/... resolved by Pydantic AI from the prefix


def _agent():
    from pydantic_ai import Agent

    return Agent(
        _model(),
        output_type=ExtractedEvent,
        system_prompt=SYSTEM,
        model_settings={"temperature": 0, "timeout": REQUEST_TIMEOUT},
        retries=1,
    )


def _usage_str(result) -> str:
    """Best-effort token counts from a pydantic-ai result. Attribute names differ
    across versions (request/response_tokens pre-v1, input/output_tokens in v1),
    so probe both. Returns '' if usage isn't available."""
    try:
        u = result.usage
        # In current pydantic-ai `usage` is a property (the RunUsage object); pre-v1
        # it was a method. Only call it if it isn't already the usage object, so we
        # don't trip the "usage() is deprecated" warning.
        if callable(u) and not hasattr(u, "input_tokens") and not hasattr(u, "request_tokens"):
            u = u()
    except Exception:  # noqa: BLE001 - usage is logging-only, never fail the call
        return ""

    def pick(*names):
        for n in names:
            v = getattr(u, n, None)
            if v is not None:
                return v
        return None

    inp = pick("input_tokens", "request_tokens")
    out = pick("output_tokens", "response_tokens")
    tot = pick("total_tokens")
    parts = [f"{label}={v}" for label, v in (("in", inp), ("out", out), ("total", tot)) if v]
    return ", ".join(parts)


def extract_event(text: str, url: str | None = None, title: str = "", agent=None) -> dict:
    """Run extraction. `agent` is injectable for tests (must expose .run_sync)."""
    agent = agent or _agent()
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    log.info("llm: model=%s, %d chars — calling…", model, len(text))
    t = time.perf_counter()
    try:
        result = agent.run_sync(build_prompt(title, text, url))
    except Exception:
        log.exception("llm: call FAILED after %.1fs (model=%s)", time.perf_counter() - t, model)
        raise
    usage = _usage_str(result)
    log.info("llm: call ok in %.1fs%s", time.perf_counter() - t, f" ({usage})" if usage else "")
    return _to_event_dict(result.output, url)


def scrape(url: str, agent=None) -> dict:
    import requests

    log.info("llm: fetching %s", url)
    t = time.perf_counter()
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    title, text = page_text(r.text)
    log.info("llm: fetched %s in %.1fs (%d chars)", url, time.perf_counter() - t, len(text))
    return extract_event(text, url, title, agent=agent)
