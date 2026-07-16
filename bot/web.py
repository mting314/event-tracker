"""Web app: log in with Discord and manage your event subscriptions.

The Discord bot stores subscriptions in SQLite keyed by Discord user id. This
aiohttp app lets the same user sign in on the website (Discord OAuth2, ``identify``
scope only) and see / edit those exact subscriptions and reminder settings — one
source of truth, shared with the bot via the same DB file.

It runs as its own process (a second ``docker compose`` service on the same volume
as the bot), so the Discord *gateway* being unreachable locally doesn't matter:
OAuth is plain HTTPS to discord.com's REST API. Run standalone with::

    uv run --extra bot python -m bot.web        # serves on WEB_PORT (default 8080)

Config (env):
    DISCORD_CLIENT_ID       OAuth2 app client id           (required for login)
    DISCORD_CLIENT_SECRET   OAuth2 app client secret       (required for login)
    OAUTH_REDIRECT_URI      full callback URL registered in the Discord app
                            (default: WEB_BASE_URL + /auth/callback)
    WEB_BASE_URL            public base URL of this app     (default http://localhost:PORT)
    SESSION_SECRET          HMAC key signing the session cookie (set in prod so
                            logins survive restarts; a random one is used if unset)
    SITE_URL                static site base — used for the stylesheet + a link back
    EVENTS_SOURCE           events.json URL/path (shared with the bot)
    DB_PATH                 SQLite path (shared with the bot; default bot/tracker.db)
    WEB_HOST / WEB_PORT     bind address (default 0.0.0.0 / 8080)
    COOKIE_SECURE           "0" to allow the cookie over plain http (local dev only)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from .db import DB
from .reminders import DEFAULT_LEAD_SECONDS
from .sync import all_series, load_events

log = logging.getLogger("web")


def _setup_tls() -> None:
    """Verify TLS via the OS trust store so the server-side calls to discord.com work
    behind corporate TLS interception / proxies (managed laptops present Discord's cert
    signed by an internal root CA that certifi's public bundle lacks). Mirrors the bot's
    setup. Falls back to certifi if truststore is unavailable."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 - fall back to a public CA bundle
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())


DISCORD_API = "https://discord.com/api"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = f"{DISCORD_API}/oauth2/token"
USER_URL = f"{DISCORD_API}/users/@me"
OAUTH_SCOPE = "identify"

SESSION_COOKIE = "ll_session"
SESSION_TTL = 30 * 24 * 3600  # 30 days
STATE_TTL = 600  # 10 minutes to complete the OAuth round-trip

STATIC_DIR = os.path.join(os.path.dirname(__file__), "web_static")

# App-config keys (stored on the aiohttp Application so tests can inject them).
# Typed AppKeys are aiohttp's recommended idiom (no NotAppKeyWarning).
K_DB: web.AppKey[DB] = web.AppKey("db", DB)
K_SECRET: web.AppKey[str] = web.AppKey("session_secret", str)
K_CLIENT_ID: web.AppKey[str] = web.AppKey("client_id", str)
K_CLIENT_SECRET: web.AppKey[str] = web.AppKey("client_secret", str)
K_REDIRECT: web.AppKey[str] = web.AppKey("redirect_uri", str)
K_SITE_URL: web.AppKey[str] = web.AppKey("site_url", str)
K_COOKIE_SECURE: web.AppKey[bool] = web.AppKey("cookie_secure", bool)
K_EVENTS_SOURCE: web.AppKey[object] = web.AppKey("events_source", object)


# --------------------------------------------------------------------------- #
# Signed tokens (stateless sessions + OAuth state) — stdlib HMAC, no deps.
# --------------------------------------------------------------------------- #
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(payload: dict, secret: str) -> str:
    """`<b64(json)>.<b64(hmac)>` — tamper-evident, not encrypted (holds no secrets)."""
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64e(sig)}"


def verify_token(token: str, secret: str) -> dict | None:
    """Return the payload iff the signature checks out and it hasn't expired."""
    try:
        body, sig = token.split(".", 1)
    except (ValueError, AttributeError):
        return None
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64d(sig), expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def _avatar_url(uid: str, avatar: str | None) -> str:
    if avatar:
        ext = "gif" if avatar.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.{ext}?size=64"
    # Default avatar bucket for the new username system.
    idx = (int(uid) >> 22) % 6 if uid.isdigit() else 0
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


# --------------------------------------------------------------------------- #
# Auth helpers
# --------------------------------------------------------------------------- #
def current_user(request: web.Request) -> dict | None:
    """The logged-in user from the session cookie, or None."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return verify_token(token, request.app[K_SECRET])


def _set_session(resp: web.Response, request: web.Request, user: dict) -> None:
    payload = {
        "uid": str(user["id"]),
        "un": user.get("global_name") or user.get("username") or "",
        "av": user.get("avatar"),
        "exp": int(time.time()) + SESSION_TTL,
    }
    resp.set_cookie(
        SESSION_COOKIE,
        sign_token(payload, request.app[K_SECRET]),
        max_age=SESSION_TTL,
        httponly=True,
        secure=request.app[K_COOKIE_SECURE],
        samesite="Lax",
    )


def _require_user(request: web.Request) -> dict:
    user = current_user(request)
    if not user:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "not logged in"}), content_type="application/json"
        )
    return user


# --------------------------------------------------------------------------- #
# Events catalog (shared with the bot) — small TTL cache.
# --------------------------------------------------------------------------- #
_events_cache: list[dict] = []
_events_at: float = 0.0
_EVENTS_TTL = 300.0


def get_events(request: web.Request) -> list[dict]:
    global _events_cache, _events_at
    now = time.monotonic()
    if not _events_cache or (now - _events_at) > _EVENTS_TTL:
        try:
            _events_cache = load_events(request.app[K_EVENTS_SOURCE])
            _events_at = now
        except Exception as exc:  # keep serving the stale copy if a refresh fails
            log.warning("events refresh failed: %s", exc)
    return _events_cache


def _event_name(events: list[dict], eid: str) -> str:
    for ev in events:
        if ev.get("id") == eid:
            return ev.get("name", eid)
    return eid


# --------------------------------------------------------------------------- #
# Routes: auth
# --------------------------------------------------------------------------- #
async def auth_login(request: web.Request) -> web.Response:
    client_id = request.app[K_CLIENT_ID]
    if not client_id:
        return web.Response(status=503, text="Discord login is not configured.")
    nxt = request.query.get("next", "/")
    if not nxt.startswith("/"):  # only allow same-app relative redirects
        nxt = "/"
    state = sign_token(
        {"n": secrets.token_urlsafe(8), "next": nxt, "exp": int(time.time()) + STATE_TTL},
        request.app[K_SECRET],
    )
    params = {
        "client_id": client_id,
        "redirect_uri": request.app[K_REDIRECT],
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "state": state,
        "prompt": "none",  # don't re-prompt a user who already authorized
    }
    raise web.HTTPFound(f"{AUTHORIZE_URL}?{urlencode(params)}")


async def auth_callback(request: web.Request) -> web.Response:
    err = request.query.get("error")
    if err:
        return web.Response(status=400, text=f"Discord login failed: {err}")
    code = request.query.get("code")
    state = request.query.get("state", "")
    if not code or not verify_token(state, request.app[K_SECRET]):
        return web.Response(status=400, text="Invalid or expired login state. Try again.")
    nxt = verify_token(state, request.app[K_SECRET]).get("next", "/")

    data = {
        "client_id": request.app[K_CLIENT_ID],
        "client_secret": request.app[K_CLIENT_SECRET],
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": request.app[K_REDIRECT],
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(TOKEN_URL, data=data, headers=headers) as r:
                if r.status != 200:
                    body = await r.text()
                    log.warning("token exchange failed %s: %s", r.status, body[:200])
                    return web.Response(status=502, text="Discord token exchange failed.")
                token = await r.json()
            access = token.get("access_token")
            async with sess.get(USER_URL, headers={"Authorization": f"Bearer {access}"}) as r:
                if r.status != 200:
                    return web.Response(status=502, text="Could not fetch your Discord profile.")
                user = await r.json()
    except aiohttp.ClientError as exc:
        # e.g. discord.com blocked/TLS-intercepted (corporate proxy). The bot hits
        # this too, which is why it runs on the VM — see the truststore note below.
        log.warning("cannot reach discord.com: %s", exc)
        return web.Response(
            status=502,
            text="Could not reach discord.com to complete login. If you're behind a "
            "corporate proxy, run this on the VM (where discord.com is reachable).",
        )

    resp = web.HTTPFound(nxt)
    _set_session(resp, request, user)
    raise resp


async def auth_logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/")
    resp.del_cookie(SESSION_COOKIE)
    raise resp


# --------------------------------------------------------------------------- #
# Routes: API (JSON)
# --------------------------------------------------------------------------- #
async def api_me(request: web.Request) -> web.Response:
    user = current_user(request)
    if not user:
        return web.json_response({"logged_in": False})
    return web.json_response(
        {
            "logged_in": True,
            "uid": user["uid"],
            "username": user.get("un") or "Discord user",
            "avatar_url": _avatar_url(user["uid"], user.get("av")),
        }
    )


async def api_events(request: web.Request) -> web.Response:
    events = get_events(request)
    return web.json_response(
        {
            "events": [
                {
                    "id": ev.get("id"),
                    "name": ev.get("name"),
                    "name_en": ev.get("name_en") or ev.get("name"),
                    "series": ev.get("series", []),
                }
                for ev in events
            ],
            "series": all_series(events),
        }
    )


async def api_subscriptions_get(request: web.Request) -> web.Response:
    user = _require_user(request)
    db: DB = request.app[K_DB]
    events = get_events(request)
    subs = db.list_subscriptions(user["uid"])
    return web.json_response(
        {
            "events": sorted(
                (
                    {"target": s["target"], "name": _event_name(events, s["target"])}
                    for s in subs
                    if s["kind"] == "event"
                ),
                key=lambda x: x["name"].lower(),
            ),
            "series": sorted(
                ({"target": s["target"]} for s in subs if s["kind"] == "series"),
                key=lambda x: x["target"].lower(),
            ),
        }
    )


def _read_sub_body(body: dict) -> tuple[str, str]:
    kind = (body.get("kind") or "").strip()
    target = (body.get("target") or "").strip()
    if kind not in ("event", "series") or not target:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "kind must be 'event'|'series' and target non-empty"}),
            content_type="application/json",
        )
    return kind, target


async def api_subscriptions_post(request: web.Request) -> web.Response:
    user = _require_user(request)
    db: DB = request.app[K_DB]
    body = await request.json()
    kind, target = _read_sub_body(body)
    if kind == "event" and not any(e.get("id") == target for e in get_events(request)):
        raise web.HTTPBadRequest(
            text=json.dumps({"error": f"unknown event id: {target}"}),
            content_type="application/json",
        )
    added = db.add_subscription(user["uid"], kind, target)
    return web.json_response({"ok": True, "added": added})


async def api_subscriptions_delete(request: web.Request) -> web.Response:
    user = _require_user(request)
    db: DB = request.app[K_DB]
    body = await request.json()
    kind, target = _read_sub_body(body)
    removed = db.remove_subscription(user["uid"], kind, target)
    return web.json_response({"ok": True, "removed": removed})


async def api_settings_get(request: web.Request) -> web.Response:
    user = _require_user(request)
    db: DB = request.app[K_DB]
    s = db.get_settings(user["uid"], DEFAULT_LEAD_SECONDS)
    return web.json_response(s)


async def api_settings_put(request: web.Request) -> web.Response:
    user = _require_user(request)
    db: DB = request.app[K_DB]
    body = await request.json()
    lead_times = body.get("lead_times")
    dm_enabled = body.get("dm_enabled")
    if lead_times is not None:
        if not isinstance(lead_times, list) or not all(
            isinstance(x, int) and x > 0 for x in lead_times
        ):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "lead_times must be a list of positive seconds"}),
                content_type="application/json",
            )
        lead_times = sorted(set(lead_times), reverse=True)
    if dm_enabled is not None:
        dm_enabled = bool(dm_enabled)
    db.set_settings(user["uid"], lead_times=lead_times, dm_enabled=dm_enabled)
    return web.json_response(db.get_settings(user["uid"], DEFAULT_LEAD_SECONDS))


# --------------------------------------------------------------------------- #
# Routes: pages
# --------------------------------------------------------------------------- #
async def page_index(request: web.Request) -> web.Response:
    html = _render_page(request)
    return web.Response(text=html, content_type="text/html")


_PAGE_TEMPLATE: str | None = None


def _render_page(request: web.Request) -> str:
    global _PAGE_TEMPLATE
    if _PAGE_TEMPLATE is None:
        with open(os.path.join(STATIC_DIR, "subscriptions.html"), encoding="utf-8") as fh:
            _PAGE_TEMPLATE = fh.read()
    site = request.app[K_SITE_URL]
    return _PAGE_TEMPLATE.replace("{{SITE_URL}}", site)


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #
def create_app(
    *,
    db: DB | None = None,
    db_path: str | None = None,
    session_secret: str | None = None,
    client_id: str = "",
    client_secret: str = "",
    redirect_uri: str = "",
    site_url: str = "",
    events_source: str | None = None,
    cookie_secure: bool = True,
) -> web.Application:
    app = web.Application()
    app[K_DB] = db or DB(db_path or os.environ.get("DB_PATH", "bot/tracker.db"))
    app[K_SECRET] = session_secret or secrets.token_urlsafe(32)
    app[K_CLIENT_ID] = client_id
    app[K_CLIENT_SECRET] = client_secret
    app[K_REDIRECT] = redirect_uri
    app[K_SITE_URL] = site_url.rstrip("/")
    app[K_EVENTS_SOURCE] = events_source
    app[K_COOKIE_SECURE] = cookie_secure

    app.add_routes(
        [
            web.get("/", page_index),
            web.get("/healthz", healthz),
            web.get("/auth/login", auth_login),
            web.get("/auth/callback", auth_callback),
            web.post("/auth/logout", auth_logout),
            web.get("/api/me", api_me),
            web.get("/api/events", api_events),
            web.get("/api/subscriptions", api_subscriptions_get),
            web.post("/api/subscriptions", api_subscriptions_post),
            web.delete("/api/subscriptions", api_subscriptions_delete),
            web.get("/api/settings", api_settings_get),
            web.put("/api/settings", api_settings_put),
        ]
    )
    return app


def _build_from_env() -> web.Application:
    port = int(os.environ.get("WEB_PORT", "8080"))
    base = os.environ.get("WEB_BASE_URL", f"http://localhost:{port}").rstrip("/")
    redirect = os.environ.get("OAUTH_REDIRECT_URI") or f"{base}/auth/callback"
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        log.warning(
            "SESSION_SECRET is unset — using an ephemeral key; logins will not survive a restart."
        )
    return create_app(
        session_secret=secret,
        client_id=os.environ.get("DISCORD_CLIENT_ID", ""),
        client_secret=os.environ.get("DISCORD_CLIENT_SECRET", ""),
        redirect_uri=redirect,
        site_url=os.environ.get("SITE_URL", ""),
        events_source=os.environ.get("EVENTS_SOURCE"),
        cookie_secure=os.environ.get("COOKIE_SECURE", "1") != "0",
    )


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _setup_tls()
    app = _build_from_env()
    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_PORT", "8080"))
    log.info("serving on %s:%s", host, port)
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()
