"""Offline tests for the website's Discord-login + subscription API (bot/web.py).

No real Discord calls: the OAuth *redirect* is asserted by URL, and the logged-in
API is exercised by forging a session cookie signed with the app's own secret
(exactly what a real login would mint), so the whole CRUD surface is covered
without the network.
"""

from __future__ import annotations

import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot import web
from bot.db import DB

SECRET = "test-secret-key"


def _events_file(tmp_path):
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            {
                "events": [
                    {"id": "ev-a", "name": "Alpha Live", "series": ["Liella!"]},
                    {"id": "ev-b", "name": "Beta Live", "series": ["Aqours"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Reset the module-level events cache so each test sees its own catalog.
    monkeypatch.setattr(web, "_events_cache", [])
    monkeypatch.setattr(web, "_events_at", 0.0)
    db = DB(tmp_path / "t.db")
    application = web.create_app(
        db=db,
        session_secret=SECRET,
        client_id="cid",
        client_secret="csecret",
        redirect_uri="https://example.com/auth/callback",
        site_url="https://example.com/site",
        events_source=_events_file(tmp_path),
        cookie_secure=False,
    )
    yield application
    db.close()


def _session_cookie(uid="u1", name="Tester"):
    token = web.sign_token(
        {"uid": uid, "un": name, "av": None, "exp": int(time.time()) + 3600}, SECRET
    )
    return {web.SESSION_COOKIE: token}


# ---------------- signed tokens ----------------


def test_token_roundtrip_tamper_expiry():
    tok = web.sign_token({"uid": "abc", "exp": int(time.time()) + 60}, SECRET)
    assert web.verify_token(tok, SECRET)["uid"] == "abc"
    assert web.verify_token(tok, "wrong-secret") is None  # bad signature
    assert web.verify_token(tok + "x", SECRET) is None  # tampered
    expired = web.sign_token({"uid": "abc", "exp": int(time.time()) - 1}, SECRET)
    assert web.verify_token(expired, SECRET) is None


def test_avatar_url_default_and_custom():
    assert web._avatar_url("123", "abcdef").startswith("https://cdn.discordapp.com/avatars/123/")
    assert "embed/avatars" in web._avatar_url("123", None)


# ---------------- auth ----------------


async def test_login_redirects_to_discord(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/auth/login", allow_redirects=False)
        assert resp.status == 302
        loc = resp.headers["Location"]
        assert loc.startswith("https://discord.com/oauth2/authorize")
        assert "client_id=cid" in loc
        assert "scope=identify" in loc


async def test_login_unconfigured_returns_503(tmp_path):
    application = web.create_app(
        db=DB(tmp_path / "t.db"), session_secret=SECRET, client_id="", events_source=None
    )
    async with TestClient(TestServer(application)) as client:
        resp = await client.get("/auth/login", allow_redirects=False)
        assert resp.status == 503


async def test_callback_rejects_bad_state(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/auth/callback?code=x&state=forged", allow_redirects=False)
        assert resp.status == 400


# ---------------- me ----------------


async def test_me_logged_out_and_in(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/me")
        assert (await resp.json())["logged_in"] is False

        resp = await client.get("/api/me", cookies=_session_cookie(name="Tester"))
        data = await resp.json()
        assert data["logged_in"] is True
        assert data["uid"] == "u1" and data["username"] == "Tester"


# ---------------- events catalog ----------------


async def test_events_catalog(app):
    async with TestClient(TestServer(app)) as client:
        data = await (await client.get("/api/events")).json()
        assert {e["id"] for e in data["events"]} == {"ev-a", "ev-b"}
        assert data["series"] == ["Aqours", "Liella!"]


# ---------------- subscriptions CRUD ----------------


async def test_subscriptions_require_login(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/subscriptions")
        assert resp.status == 401


async def test_subscription_crud_flow(app):
    cookies = _session_cookie()
    async with TestClient(TestServer(app)) as client:
        # empty to start
        data = await (await client.get("/api/subscriptions", cookies=cookies)).json()
        assert data["events"] == [] and data["series"] == []

        # add an event + a series
        r = await client.post(
            "/api/subscriptions", json={"kind": "event", "target": "ev-a"}, cookies=cookies
        )
        assert (await r.json())["added"] is True
        await client.post(
            "/api/subscriptions", json={"kind": "series", "target": "Liella!"}, cookies=cookies
        )

        data = await (await client.get("/api/subscriptions", cookies=cookies)).json()
        assert data["events"] == [{"target": "ev-a", "name": "Alpha Live"}]
        assert data["series"] == [{"target": "Liella!"}]

        # dupe add is a no-op
        r = await client.post(
            "/api/subscriptions", json={"kind": "event", "target": "ev-a"}, cookies=cookies
        )
        assert (await r.json())["added"] is False

        # remove
        r = await client.delete(
            "/api/subscriptions", json={"kind": "event", "target": "ev-a"}, cookies=cookies
        )
        assert (await r.json())["removed"] is True
        data = await (await client.get("/api/subscriptions", cookies=cookies)).json()
        assert data["events"] == []


async def test_subscription_rejects_unknown_event_and_bad_kind(app):
    cookies = _session_cookie()
    async with TestClient(TestServer(app)) as client:
        r = await client.post(
            "/api/subscriptions", json={"kind": "event", "target": "nope"}, cookies=cookies
        )
        assert r.status == 400
        r = await client.post(
            "/api/subscriptions", json={"kind": "bogus", "target": "x"}, cookies=cookies
        )
        assert r.status == 400


async def test_subscriptions_are_per_user(app):
    async with TestClient(TestServer(app)) as client:
        await client.post(
            "/api/subscriptions",
            json={"kind": "event", "target": "ev-a"},
            cookies=_session_cookie(uid="u1"),
        )
        data = await (
            await client.get("/api/subscriptions", cookies=_session_cookie(uid="u2"))
        ).json()
        assert data["events"] == []  # u2 doesn't see u1's subs


# ---------------- settings ----------------


async def test_settings_get_and_put(app):
    cookies = _session_cookie()
    async with TestClient(TestServer(app)) as client:
        data = await (await client.get("/api/settings", cookies=cookies)).json()
        assert data["dm_enabled"] is True  # default

        r = await client.put(
            "/api/settings",
            json={"lead_times": [3600, 86400], "dm_enabled": False},
            cookies=cookies,
        )
        data = await r.json()
        assert data["lead_times"] == [86400, 3600]  # sorted desc, deduped
        assert data["dm_enabled"] is False

        # bad lead times rejected
        r = await client.put("/api/settings", json={"lead_times": [-5]}, cookies=cookies)
        assert r.status == 400


# ---------------- page ----------------


async def test_index_page_renders_and_bakes_site_url(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        assert resp.status == 200
        body = await resp.text()
        assert "My Subscriptions" in body
        assert "https://example.com/site" in body  # SITE_URL substituted
        assert "{{SITE_URL}}" not in body
