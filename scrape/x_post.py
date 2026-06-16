"""Best-effort helper for X (Twitter) announcements.

X blocks unauthenticated HTML scraping, so we read posts through the public
**syndication endpoint** (``cdn.syndication.twimg.com/tweet-result``) that the
official embed widget uses. It returns the full post text *and* the expanded
(un-shortened) URLs the post links to — which matters because LL ticket
announcements usually carry only a teaser plus a link to the real event page.

Those links are surfaced as ``source_links`` so the ingest dispatcher can follow
the best one and pull the structured details from there (the post is the trigger;
the linked official/FC page is the source of truth). Date hints in the post text
are still parsed into suggested rounds as a fallback.

Parsing (``parse_text``) is kept network-free so it stays unit-testable offline;
only ``scrape``/``fetch`` touch the network.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from urllib.parse import urlparse

from .util import find_all_dates, parse_datetime

UA = "Mozilla/5.0 (compatible; ll-lottery-tracker/0.1)"
_SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"

# Logged-out GraphQL fallback for **long-form "note tweets"**: the syndication
# embed API truncates them and returns the body's link card only as an opaque
# note_tweet id. The public web client reads them with a guest token + the web
# app's (public, hard-coded) bearer — no dev account or secret. Brittle: if X
# rotates the bearer or the query id, the call 4xx's and we degrade to syndication.
_WEB_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_GQL_TWEET = "https://api.twitter.com/graphql/0hWvDhmW8YQ-S_ib3azIrw/TweetResultByRestId"
_GUEST_ACTIVATE = "https://api.twitter.com/1.1/guest/activate.json"

# URLs inside free post text (trailing punctuation is trimmed below).
_URL_RE = re.compile(r"https?://[^\s<>\"'）)]+")
# Hosts that are never the event page: X itself, its shortener, and media CDNs.
_SKIP_HOSTS = ("x.com", "twitter.com", "t.co", "twimg.com", "pic.twitter.com", "pic.x.com")
# Known event/ticket domains, in the order we prefer to follow them.
_PREFERRED_HOSTS = (
    "lovelive-anime.jp",
    "ll-fans.jp",
    "eventernote.com",
    "eplus.jp",
    "l-tike.com",
    "pia.jp",
    "lawson.co.jp",
)

# Year-less show dates as written on X: "6/13", "6/13-14" (a same-month range).
_MD_RANGE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*[-–—ー〜～~]\s*(\d{1,2})")
_MD = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\s*/?\d)")
# A line names a *show* date when it mentions the venue/holding; sale & lottery
# windows use these keywords and must NOT be read as performance dates.
_HELD_KW = ("開催", "＠", "@", "会場", "公演")
_SALE_KW = ("発売", "受付", "申込", "締切", "抽選", "販売", "先行", "支払", "入金")


def _tweet_id(url: str) -> str | None:
    """Pull the numeric status id out of an x.com / twitter.com URL."""
    m = re.search(r"/status(?:es)?/(\d+)", url or "")
    return m.group(1) if m else None


def _float_base36(n: float) -> str:
    """Mimic JS ``Number.prototype.toString(36)`` for the syndication token."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    neg = n < 0
    n = abs(n)
    intpart = int(n)
    frac = n - intpart
    ip = ""
    while intpart:
        ip = digits[intpart % 36] + ip
        intpart //= 36
    ip = ip or "0"
    fp = ""
    if frac:
        fp = "."
        for _ in range(20):
            frac *= 36
            d = int(frac)
            fp += digits[d]
            frac -= d
            if frac <= 0:
                break
    return ("-" if neg else "") + ip + fp


def _syndication_token(tweet_id: str) -> str:
    """The token the embed widget derives client-side from the tweet id."""
    return re.sub(r"[0.]", "", _float_base36((int(tweet_id) / 1e15) * math.pi)) or "x"


def _followable(link: str) -> bool:
    """True for an external link worth following for event details."""
    host = urlparse(link).netloc.lower()
    if not host:
        return False
    return not any(host == h or host.endswith("." + h) for h in _SKIP_HOSTS)


# Path hints: a ticket/live page is the right target for a ticket post; a movie /
# news / goods page on the same host is usually tangential and ranked lower.
_PATH_UP = ("ticket", "/live", "live_detail", "/event", "/tour", "ticket.")
_PATH_DOWN = ("/movie", "/news", "/goods", "/bd", "/dvd", "/cd", "/blog")


def link_priority(link: str) -> tuple[int, int]:
    """Sort key: known event/ticket hosts first, then ticket/live paths before
    tangential ones (movie/news/goods) on that host."""
    host = urlparse(link).netloc.lower()
    host_rank = next(
        (i for i, h in enumerate(_PREFERRED_HOSTS) if host == h or host.endswith("." + h)),
        len(_PREFERRED_HOSTS),
    )
    low = link.lower()
    if any(k in low for k in _PATH_UP):
        path_rank = 0
    elif any(k in low for k in _PATH_DOWN):
        path_rank = 2
    else:
        path_rank = 1
    return (host_rank, path_rank)


def extract_links(text: str) -> list[str]:
    """Followable external URLs found in free post text (deduped, trimmed)."""
    out, seen = [], set()
    for raw in _URL_RE.findall(text or ""):
        link = raw.rstrip(".,;、。")
        if link not in seen and _followable(link):
            seen.add(link)
            out.append(link)
    return out


def _loose_event_dates(text: str, year: int) -> list[date]:
    """Year-less show dates (``6/13``, ``6/13-14``) from venue/holding lines only.

    X posts write the show date without a year and alongside the venue ("6/13-14＠
    京王アリーナ TOKYO"); sale/lottery dates ("発売日：5/30") share the format, so we
    only read lines that look like a performance and never a sale window. ``year``
    comes from the tweet's own timestamp.
    """
    out, seen = [], set()

    def add(mo: int, d: int) -> None:
        if not (1 <= mo <= 12):
            return
        try:
            dt = date(year, mo, d)
        except ValueError:
            return
        if dt not in seen:
            seen.add(dt)
            out.append(dt)

    for line in (text or "").splitlines():
        if not any(k in line for k in _HELD_KW) or any(k in line for k in _SALE_KW):
            continue
        rng = _MD_RANGE.search(line)
        if rng:
            mo, d1, d2 = (int(g) for g in rng.groups())
            if d1 <= d2:
                for d in range(d1, d2 + 1):
                    add(mo, d)
            continue
        for m in _MD.finditer(line):
            add(int(m.group(1)), int(m.group(2)))
    return out


def parse_text(
    text: str,
    url: str | None = None,
    links: list[str] | None = None,
    default_year: int | None = None,
) -> dict:
    """Pull round hints + nested links out of an announcement.

    ``links`` are pre-extracted (e.g. expanded URLs from the syndication API);
    they're merged with any URLs found in ``text`` and prioritised so the
    dispatcher follows the likeliest event page first. ``default_year`` (the
    tweet's year) lets us recover year-less show dates like "6/13-14".
    """
    suggestions = []
    for line in (text or "").splitlines():
        dt = parse_datetime(line)
        if dt:
            suggestions.append({"name": line.strip()[:60], "apply_deadline": dt})

    merged, seen = [], set()
    for link in [*(links or []), *extract_links(text)]:
        if _followable(link) and link not in seen:
            seen.add(link)
            merged.append(link)
    merged.sort(key=link_priority)

    event_dates = find_all_dates(text)
    if not event_dates and default_year:
        event_dates = _loose_event_dates(text, default_year)

    return {
        "official_url": url,
        "event_dates": event_dates,
        "rounds": suggestions,
        "source_links": merged,
        "notes": "TODO drafted from X post — verify every date",
    }


# Subtrees describing the post's *author*, not the post. We prune these while
# walking so we never pick up the account's bio link, profile text, or account
# creation date — only what's actually in the post the user gave us.
_AUTHOR_KEYS = ("user_results", "user")


def _walk(obj, key: str) -> list:
    """Collect every string value under ``key`` in nested JSON, skipping author subtrees."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _AUTHOR_KEYS:
                continue  # don't descend into the tweet author's profile
            if k == key and isinstance(v, str):
                out.append(v)
            else:
                out += _walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            out += _walk(v, key)
    return out


def _from_payload(payload: dict) -> tuple[str, list[str], int | None]:
    """Extract the post's text, its expanded links, and its year from any X JSON.

    Works for both the syndication ``tweet-result`` payload and the GraphQL
    ``TweetResultByRestId`` payload by walking for ``expanded_url`` / text /
    ``created_at`` fields, so it's resilient to X's nested, reshuffled structures.
    The walk skips the author subtree (see ``_AUTHOR_KEYS``) so we only ever read
    links/text/date that belong to the **post itself** — not the account's bio
    link (which is a common false positive) or the account's creation date.
    """
    texts = _walk(payload, "text") + _walk(payload, "full_text")
    text = max(texts, key=len) if texts else ""
    links = list(dict.fromkeys(_walk(payload, "expanded_url") + _walk(payload, "url")))
    year = None
    for created in _walk(payload, "created_at"):
        m = re.search(r"(20\d{2})", created)
        if m:
            year = int(m.group(1))
            break
    return text, links, year


def _syndication_payload(tweet_id: str) -> dict:
    """Raw tweet-result JSON from the no-auth embed API."""
    import requests

    resp = requests.get(
        _SYNDICATION,
        params={"id": tweet_id, "token": _syndication_token(tweet_id), "lang": "en"},
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_syndication(tweet_id: str) -> tuple[str, list[str], int | None]:
    """The cheap, no-auth embed API — enough for normal tweets."""
    return _from_payload(_syndication_payload(tweet_id))


_guest_token: str | None = None  # cached across calls so the bot doesn't re-activate each time


def _guest(force: bool = False) -> str:
    """Return a (cached) guest token, activating a fresh one when needed."""
    global _guest_token
    if _guest_token and not force:
        return _guest_token
    import requests

    g = requests.post(
        _GUEST_ACTIVATE,
        headers={"Authorization": f"Bearer {_WEB_BEARER}", "User-Agent": UA},
        timeout=20,
    )
    g.raise_for_status()
    _guest_token = g.json()["guest_token"]
    return _guest_token


def _graphql_payload(tweet_id: str) -> dict:
    """Raw TweetResultByRestId JSON via a logged-out guest token."""
    import requests

    variables = {
        "tweetId": tweet_id,
        "withCommunity": False,
        "includePromotedContent": False,
        "withVoice": False,
    }
    on = (
        "creator_subscriptions_tweet_preview_api_enabled tweetypie_unmention_optimization_enabled "
        "responsive_web_edit_tweet_api_enabled graphql_is_translatable_rweb_tweet_is_translatable_enabled "
        "view_counts_everywhere_api_enabled longform_notetweets_consumption_enabled "
        "responsive_web_twitter_article_tweet_consumption_enabled freedom_of_speech_not_reach_fetch_enabled "
        "standardized_nudges_misinfo tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled "
        "longform_notetweets_rich_text_read_enabled longform_notetweets_inline_media_enabled "
        "responsive_web_graphql_exclude_directive_enabled responsive_web_graphql_timeline_navigation_enabled"
    ).split()
    off = (
        "tweet_awards_web_tipping_enabled verified_phone_label_enabled "
        "responsive_web_media_download_video_enabled "
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled "
        "responsive_web_enhance_cards_enabled"
    ).split()
    features = {**dict.fromkeys(on, True), **dict.fromkeys(off, False)}
    params = {"variables": json.dumps(variables), "features": json.dumps(features)}

    # One retry with a fresh guest token covers an expired/throttled cached token.
    resp = None
    for attempt in range(2):
        headers = {
            "Authorization": f"Bearer {_WEB_BEARER}",
            "User-Agent": UA,
            "x-guest-token": _guest(force=attempt > 0),
        }
        resp = requests.get(_GQL_TWEET, params=params, headers=headers, timeout=20)
        if resp.status_code in (401, 403, 429) and attempt == 0:
            continue
        break
    resp.raise_for_status()
    return resp.json()


def _fetch_graphql(tweet_id: str) -> tuple[str, list[str], int | None]:
    """Logged-out GraphQL read — recovers long-form note-tweet bodies + link cards."""
    return _from_payload(_graphql_payload(tweet_id))


def fetch(url: str) -> tuple[str, list[str], int | None]:
    """Return (post text, expanded links, post year) for an X post.

    Tries the cheap embed API first; if it surfaces no followable link (the case
    for long-form note tweets, whose link card it hides), retries via the
    logged-out GraphQL read. Falls back to a raw GET so ``--text`` still works.
    """
    tweet_id = _tweet_id(url)
    text, links, year = "", [], None
    if tweet_id:
        try:
            text, links, year = _fetch_syndication(tweet_id)
        except Exception:  # noqa: BLE001 - try GraphQL / raw GET below
            pass
        if not any(_followable(link) for link in links):
            try:
                g_text, g_links, g_year = _fetch_graphql(tweet_id)
                if g_links or len(g_text) > len(text):
                    text = g_text or text
                    links = list(dict.fromkeys(links + g_links))
                    year = year or g_year
            except Exception:  # noqa: BLE001 - GraphQL is brittle; keep syndication result
                pass
        if text or links:
            return text, links, year

    # Last resort: a plain GET (usually blocked, but free text -> --text still works).
    import requests

    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - any failure -> manual fallback
        raise RuntimeError(
            f"could not fetch X post ({exc}); rerun with --text and paste the post"
        ) from exc
    return resp.text, [], None


def scrape(url: str) -> dict:
    """Fetch a post, parse date hints, and surface links to follow."""
    text, links, year = fetch(url)
    return parse_text(text, url, links, default_year=year)
