"""SQLite persistence for the Discord bot.

Everything is keyed by Discord user id, so the schema is multi-user from day one
even while only one person uses it. A thin wrapper around ``sqlite3`` — no ORM.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    user_id     TEXT PRIMARY KEY,
    lead_times  TEXT NOT NULL,           -- JSON list of seconds-before
    dm_enabled  INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind    TEXT NOT NULL,               -- 'event' | 'series'
    target  TEXT NOT NULL,               -- event id, or series tag
    UNIQUE(user_id, kind, target)
);
CREATE TABLE IF NOT EXISTS guild_channels (
    guild_id   TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent_reminders (
    user_id TEXT NOT NULL,
    occ_key TEXT NOT NULL,               -- event|round|date_type|lead
    sent_at TEXT NOT NULL,
    PRIMARY KEY (user_id, occ_key)
);
CREATE TABLE IF NOT EXISTS notified_events (
    user_id  TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY (user_id, event_id)
);
"""


class DB:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- settings ---
    def get_settings(self, user_id: str, default_leads: list[int]) -> dict:
        row = self.conn.execute(
            "SELECT lead_times, dm_enabled FROM settings WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            return {"lead_times": list(default_leads), "dm_enabled": True}
        return {"lead_times": json.loads(row["lead_times"]), "dm_enabled": bool(row["dm_enabled"])}

    def set_settings(
        self, user_id: str, lead_times: list[int] | None = None, dm_enabled: bool | None = None
    ):
        cur = self.get_settings(user_id, [])
        if lead_times is not None:
            cur["lead_times"] = lead_times
        if dm_enabled is not None:
            cur["dm_enabled"] = dm_enabled
        self.conn.execute(
            "INSERT INTO settings(user_id, lead_times, dm_enabled) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET lead_times=excluded.lead_times, dm_enabled=excluded.dm_enabled",
            (user_id, json.dumps(cur["lead_times"]), int(cur["dm_enabled"])),
        )
        self.conn.commit()

    # --- subscriptions ---
    def add_subscription(self, user_id: str, kind: str, target: str) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO subscriptions(user_id, kind, target) VALUES(?,?,?)",
                (user_id, kind, target),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # already subscribed

    def remove_subscription(self, user_id: str, kind: str, target: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM subscriptions WHERE user_id=? AND kind=? AND target=?",
            (user_id, kind, target),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_subscriptions(self, user_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT kind, target FROM subscriptions WHERE user_id=? ORDER BY kind, target",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_subscriptions(self) -> dict[str, list[dict]]:
        """{user_id: [{kind, target}, ...]} across everyone — used by the scheduler."""
        rows = self.conn.execute("SELECT user_id, kind, target FROM subscriptions").fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["user_id"], []).append({"kind": r["kind"], "target": r["target"]})
        return out

    # --- dedup ---
    def was_sent(self, user_id: str, occ_key: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM sent_reminders WHERE user_id=? AND occ_key=?", (user_id, occ_key)
            ).fetchone()
            is not None
        )

    def mark_sent(self, user_id: str, occ_key: str, sent_at: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO sent_reminders(user_id, occ_key, sent_at) VALUES(?,?,?)",
            (user_id, occ_key, sent_at),
        )
        self.conn.commit()

    # --- new-event feed ---
    def was_notified_of_event(self, user_id: str, event_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM notified_events WHERE user_id=? AND event_id=?", (user_id, event_id)
            ).fetchone()
            is not None
        )

    def mark_event_notified(self, user_id: str, event_id: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO notified_events(user_id, event_id) VALUES(?,?)",
            (user_id, event_id),
        )
        self.conn.commit()

    # --- guild channels ---
    def set_channel(self, guild_id: str, channel_id: str):
        self.conn.execute(
            "INSERT INTO guild_channels(guild_id, channel_id) VALUES(?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id",
            (guild_id, channel_id),
        )
        self.conn.commit()

    def all_channels(self) -> list[str]:
        return [
            r["channel_id"]
            for r in self.conn.execute("SELECT channel_id FROM guild_channels").fetchall()
        ]
