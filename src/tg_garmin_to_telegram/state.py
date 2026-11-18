import json
import os
import sqlite3
import threading
import time
from typing import Optional



class State:
    """SQLite persistence: users (per-user bot token, delivery target, mail token), rides, meta, events.
    Thread-safe: single connection with check_same_thread=False, guarded by an internal RLock."""

    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        # timeout=30: the ingest HTTP server may briefly share the DB with the poll loop's writes
        self.db = sqlite3.connect(os.path.join(data_dir, "state.db"), timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        # Schema bootstrap must fail fast: a half-initialised DB is worse than a k8s restart (a missing
        # `rides` table would make bookkeeping raise after the public post was already sent).
        with self._lock:
            # email_token's UNIQUE is a named partial index (see _migrate): ALTER TABLE cannot add
            # a UNIQUE constraint to an older database, and NULL tokens must not collide.
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS users ("
                "user_id INTEGER PRIMARY KEY, chat_id INTEGER, lang TEXT DEFAULT 'ru', created_ts INTEGER, "
                "email_token TEXT, email_addr TEXT, delivery_mode TEXT DEFAULT 'own', "
                "bot_token TEXT, target_type TEXT, target_id INTEGER, target_title TEXT, "
                "state TEXT DEFAULT 'setup', photos_alpha INTEGER DEFAULT 1, garmin_api_key TEXT, garmin_name TEXT)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS rides ("
                "ride_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, session_id TEXT UNIQUE, "
                "live_url TEXT, started_ts INTEGER, ended_ts INTEGER, state TEXT DEFAULT 'started', "
                "channel_msg_id INTEGER, discuss_msg_id INTEGER, activity_id TEXT)"
            )
            self.db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ts INTEGER, kind TEXT, data TEXT)"
            )
            self._migrate()
            # Secondary indexes go after _migrate(): a legacy table may not have the columns yet
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_rides_user ON rides(user_id, started_ts)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_events_user_ts ON events(user_id, ts DESC)")
            self.db.commit()

    def _migrate(self) -> None:
        """Idempotent column additions for older databases (PRAGMA check, no marker needed)."""
        users_cols = {r[1] for r in self.db.execute("PRAGMA table_info(users)")}
        for name, ddl in (
            ("lang", "TEXT DEFAULT 'ru'"),
            ("email_token", "TEXT"),
            ("email_addr", "TEXT"),
            ("delivery_mode", "TEXT DEFAULT 'own'"),
            ("bot_token", "TEXT"),
            ("target_type", "TEXT"),
            ("target_id", "INTEGER"),
            ("target_title", "TEXT"),
            ("state", "TEXT DEFAULT 'setup'"),
            ("photos_alpha", "INTEGER DEFAULT 1"),
            ("garmin_api_key", "TEXT"),
            ("garmin_name", "TEXT"),
        ):
            if name not in users_cols:
                self.db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
        idx = {r[1] for r in self.db.execute("PRAGMA index_list(users)")}
        if "uix_users_email_token" not in idx:
            self.db.execute(
                "CREATE UNIQUE INDEX uix_users_email_token ON users(email_token) "
                "WHERE email_token IS NOT NULL"
            )
        rides_cols = {r[1] for r in self.db.execute("PRAGMA table_info(rides)")}
        for name, ddl in (
            ("started_ts", "INTEGER"),
            ("live_url", "TEXT"),
            ("ended_ts", "INTEGER"),
            ("state", "TEXT DEFAULT 'started'"),
            ("channel_msg_id", "INTEGER"),
            ("discuss_msg_id", "INTEGER"),
            ("activity_id", "TEXT"),
        ):
            if name not in rides_cols:
                self.db.execute(f"ALTER TABLE rides ADD COLUMN {name} {ddl}")

    # -- meta / global settings -----------------------------------------------------------------------------------
    def get_meta(self, k: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return row["v"] if row else default

    def set_meta(self, k: str, v: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO meta(k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v))
            )
            self.db.commit()

    def delete_meta(self, k: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM meta WHERE k=?", (k,))
            self.db.commit()

    # -- users ----------------------------------------------------------------------------------------------------
    def user(self, user_id: int) -> Optional[dict]:
        with self._lock:
            row = self.db.execute("SELECT * FROM users WHERE user_id=?", (int(user_id),)).fetchone()
            return dict(row) if row else None

    def touch_user(self, user_id: int, chat_id: Optional[int] = None, lang: str = "") -> dict:
        """Make sure a row exists (first contact); returns the row. Keeps existing values (OR IGNORE)."""
        with self._lock:
            self.db.execute(
                "INSERT OR IGNORE INTO users(user_id, chat_id, created_ts) VALUES (?,?,?)",
                (int(user_id), int(chat_id) if chat_id is not None else None, int(time.time())),
            )
            if lang:
                self.db.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, int(user_id)))
            self.db.commit()
            return self.user(user_id)

    def user_by_email_token(self, token: str) -> Optional[dict]:
        with self._lock:
            row = self.db.execute("SELECT * FROM users WHERE email_token=?", (str(token),)).fetchone()
            return dict(row) if row else None

    def user_by_email(self, email_addr: str) -> Optional[dict]:
        """The user whose mailbox receives this mail (LiveTrack mail names the recipient)."""
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM users WHERE email_addr=? AND email_addr != ''", (str(email_addr),)
            ).fetchone()
            return dict(row) if row else None

    def set_email_link(self, user_id: int, email_token: str, email_addr: str) -> None:
        """Bind this user's LiveTrack mail: the secret URL token and the recipient address."""
        with self._lock:
            self.db.execute(
                "UPDATE users SET email_token=?, email_addr=? WHERE user_id=?",
                (str(email_token), str(email_addr), int(user_id)),
            )
            self.db.commit()

    def set_target(self, user_id: int, delivery_mode: str, target_type: str, target_id: Optional[int],
                   target_title: str, bot_token: str) -> None:
        """Store where and as whom this user's announcements are delivered."""
        with self._lock:
            self.db.execute(
                "UPDATE users SET delivery_mode=?, target_type=?, target_id=?, target_title=?, bot_token=? "
                "WHERE user_id=?",
                (str(delivery_mode), str(target_type),
                 int(target_id) if target_id is not None else None,
                 str(target_title), str(bot_token), int(user_id)),
            )
            self.db.commit()

    def set_user_state(self, user_id: int, state: str) -> None:
        with self._lock:
            self.db.execute("UPDATE users SET state=? WHERE user_id=?", (str(state), int(user_id)))
            self.db.commit()

    def set_lang(self, user_id: int, lang: str) -> None:
        with self._lock:
            self.db.execute("UPDATE users SET lang=? WHERE user_id=?", (str(lang), int(user_id)))
            self.db.commit()

    def set_photos_alpha(self, user_id: int, flag: bool) -> None:
        """Opt-in switch for the alpha photo-album feature (1 = on by default)."""
        with self._lock:
            self.db.execute(
                "UPDATE users SET photos_alpha=? WHERE user_id=?", (1 if flag else 0, int(user_id))
            )
            self.db.commit()

    def active_users(self) -> list:
        """Users whose delivery is fully configured: own bot token plus a concrete target chat."""
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM users WHERE state='active' AND bot_token IS NOT NULL AND bot_token != '' "
                "AND target_id IS NOT NULL ORDER BY user_id"
            ).fetchall()
            return [dict(r) for r in rows]

    # -- rides ----------------------------------------------------------------------------------------------------
    def start_ride(self, user_id: int, session_id: str, live_url: str, started_ts: int) -> bool:
        """Remember an announced ride. A duplicate session (at-least-once send) keeps the first row."""
        with self._lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO rides(user_id, session_id, live_url, started_ts, state) "
                "VALUES (?,?,?,?, 'started')",
                (int(user_id), str(session_id), str(live_url), int(started_ts)),
            )
            self.db.commit()
            return cur.rowcount > 0

    def get_ride(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self.db.execute("SELECT * FROM rides WHERE session_id=?", (str(session_id),)).fetchone()
            return dict(row) if row else None

    def set_channel_msg(self, session_id: str, channel_msg_id: int) -> bool:
        """Attach the announcement post in the user's target chat to the ride."""
        with self._lock:
            cur = self.db.execute(
                "UPDATE rides SET channel_msg_id=? WHERE session_id=? AND channel_msg_id IS NULL",
                (int(channel_msg_id), str(session_id)),
            )
            self.db.commit()
            return cur.rowcount > 0

    def set_discuss_msg(self, session_id: str, discuss_msg_id: int) -> bool:
        """Attach the auto-forwarded copy in the linked discussion group (the thread root)."""
        with self._lock:
            cur = self.db.execute(
                "UPDATE rides SET discuss_msg_id=? WHERE session_id=? AND discuss_msg_id IS NULL",
                (int(discuss_msg_id), str(session_id)),
            )
            self.db.commit()
            return cur.rowcount > 0

    def set_ride_end(self, session_id: str, ended_ts: int) -> bool:
        """Close a ride with the exact end from Garmin; only a 'started' ride transitions."""
        with self._lock:
            cur = self.db.execute(
                "UPDATE rides SET ended_ts=?, state='ended' WHERE session_id=? AND state='started'",
                (int(ended_ts), str(session_id)),
            )
            self.db.commit()
            return cur.rowcount > 0

    def set_activity(self, session_id: str, activity_id: str) -> None:
        """Store the matched Garmin activity id (ride-end matching, photo albums)."""
        with self._lock:
            self.db.execute(
                "UPDATE rides SET activity_id=? WHERE session_id=?", (str(activity_id), str(session_id))
            )
            self.db.commit()

    def user_rides(self, user_id: int, state: Optional[str] = None, limit: int = 50) -> list:
        """This user's rides, newest first; optional exact-state filter."""
        with self._lock:
            if state:
                rows = self.db.execute(
                    "SELECT * FROM rides WHERE user_id=? AND state=? ORDER BY started_ts DESC LIMIT ?",
                    (int(user_id), str(state), int(limit)),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM rides WHERE user_id=? ORDER BY started_ts DESC LIMIT ?",
                    (int(user_id), int(limit)),
                ).fetchall()
            return [dict(r) for r in rows]

    # -- events (per-user log) ------------------------------------------------------------------------------------
    def add_event(self, user_id: int, kind: str, data: dict) -> None:
        """Write an event row; keep at most 200 per user (drop oldest)."""
        with self._lock:
            self.db.execute(
                "INSERT INTO events(user_id, ts, kind, data) VALUES (?,?,?,?)",
                (int(user_id), int(time.time()), str(kind), json.dumps(data, separators=(",", ":"))),
            )
            self.db.execute(
                "DELETE FROM events WHERE user_id=? AND id NOT IN ("
                "SELECT id FROM events WHERE user_id=? ORDER BY ts DESC, id DESC LIMIT 200)",
                (int(user_id), int(user_id)),
            )
            self.db.commit()

    def events(self, user_id: int, limit: int = 30) -> list:
        with self._lock:
            rows = self.db.execute(
                "SELECT ts, kind, data FROM events WHERE user_id=? ORDER BY ts DESC, id DESC LIMIT ?",
                (int(user_id), int(limit)),
            ).fetchall()
            return [{"ts": r["ts"], "kind": r["kind"], "data": json.loads(r["data"])} for r in rows]
