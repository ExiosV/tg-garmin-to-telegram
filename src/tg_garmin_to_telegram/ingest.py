"""The ingest HTTP API: the website (and the dashboard) read and configure users through it.

Stdlib ThreadingHTTPServer like the tg-garmin-to-osm donor; bearer INGEST_TOKEN auth; no cookies,
no sessions — the website owns its own session handling and talks to this API server-to-server.
/health endpoints stay unauthenticated (no user data in them). The user rows never expose the
bot token or the Garmin key: those fields are stripped on every read."""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

log = logging.getLogger("tg_garmin_to_telegram")

PUBLIC_FIELDS = ("user_id", "chat_id", "lang", "created_ts", "email_token", "email_addr",
                 "delivery_mode", "target_type", "target_id", "target_title", "state",
                 "photos_alpha", "garmin_name")


def _strip(row: Optional[dict]) -> Optional[dict]:
    """The public shape of a user row: no bot_token, no garmin_api_key."""
    if row is None:
        return None
    return {k: v for k, v in row.items() if k in PUBLIC_FIELDS}


_START = time.time()


def _metrics(state) -> dict:
    """Tiny process/service gauges; no user data."""
    import resource
    try:
        rides = {row["state"]: row["n"] for row in
                 state.db.execute("SELECT state, COUNT(*) AS n FROM rides GROUP BY state")}
        users = state.db.execute("SELECT COUNT(*) AS n FROM users").fetchone()[0]
    except Exception as e:  # metrics must never break the endpoint
        rides, users = {}, -1
    return {
        "process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "process_uptime_seconds": round(time.time() - _START, 1),
        "users_total": users,
        "rides_by_state": rides,
    }


def serve(port: int, token: str, state, email_domain: str = "exiosv.com",
          photos_root=None) -> ThreadingHTTPServer:
    """Start the API server (caller passes it as a daemon thread). Refuses an empty token.
    email_domain backs the /api/accounts/telegram address bookkeeping; photos_root (when given)
    lets DELETE /api/users/<uid> wipe the buffered photo files too."""
    if not token:
        raise ValueError("INGEST_TOKEN is empty: refusing to start the ingest API without auth")

    class _Bound(_Handler):
        pass

    _Bound.token = token
    _Bound.state = state
    _Bound.email_domain = email_domain
    _Bound.photos_root = photos_root
    httpd = ThreadingHTTPServer(("0.0.0.0", port), _Bound)
    log.info("ingest API listening on :%s", port)
    return httpd


class _Handler(BaseHTTPRequestHandler):
    token = ""
    state = None
    email_domain = "exiosv.com"
    photos_root = None

    def log_message(self, fmt, *args):  # the default writes to stderr per request; keep it quiet
        pass

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        from hmac import compare_digest
        auth = self.headers.get("Authorization", "")
        return bool(self.token) and compare_digest(auth, f"Bearer {self.token}")

    def _uid(self) -> Optional[int]:
        try:
            return int(self.path.rstrip("/").rsplit("/", 1)[-1].split("?")[0])
        except (ValueError, IndexError):
            return None

    def do_GET(self):  # noqa: N802 (stdlib naming)
        try:
            if self.path == "/ping":
                self._json(200, {"ok": True, "ts": int(time.time())})
                return
            if self.path == "/metrics":
                self._json(200, _metrics(self.state))
                return
            if self.path.startswith("/api/") and not self._authorized():
                self._json(401, {"ok": False, "error": "unauthorized"})
                return
            if self.path.startswith("/api/users/") and self._authorized():
                uid = self._uid()
                row = self.state.user(uid) if uid is not None else None
                if row is None:
                    self._json(404, {"ok": False, "error": "unknown user"})
                    return
                self._json(200, {"ok": True, "user": _strip(row)})
                return
            self._json(404, {"ok": False, "error": "not found"})
        except Exception as e:  # never leak a traceback to the socket
            log.error("ingest GET %s failed: %s", self.path, redact_safe(e))
            self._json(500, {"ok": False, "error": "internal"})

    def do_POST(self):  # noqa: N802
        try:
            if not self._authorized():
                self._json(401, {"ok": False, "error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                self._json(400, {"ok": False, "error": "bad json"})
                return
            if not isinstance(payload, dict):
                self._json(400, {"ok": False, "error": "bad json"})
                return
            if self.path.rstrip("/") == "/api/accounts/telegram":
                # The garmin-telegram-web contract (its PR #1): {uid, chat_id, lang} -> the account
                # row with its personal LiveTrack address; the stripped row is the response.
                uid = _as_int(payload.get("uid"))
                if uid is None or uid <= 0:
                    self._json(400, {"ok": False, "error": "uid must be a positive integer"})
                    return
                chat_id = _as_int(payload.get("chat_id"))
                lang = payload.get("lang") if payload.get("lang") in ("ru", "en") else ""
                user = self.state.ensure_email_link(uid, self.email_domain,
                                                    chat_id=chat_id, lang=lang)
                self._json(200, {"ok": True, "user": _strip(user)})
                return
            if self.path.startswith("/api/users/") and self.path.rstrip("/").endswith("/target"):
                uid = self._uid_from_suffix("/target")
                row = self.state.user(uid) if uid is not None else None
                if row is None:
                    self._json(404, {"ok": False, "error": "unknown user"})
                    return
                mode = payload.get("delivery_mode")
                if mode not in ("own", "our"):
                    self._json(400, {"ok": False, "error": "delivery_mode must be own|our"})
                    return
                self.state.set_target(uid, mode, str(payload.get("target_type") or "group"),
                                      payload.get("target_id"),
                                      str(payload.get("target_title") or ""),
                                      str(payload.get("bot_token") or row.get("bot_token") or ""))
                self._json(200, {"ok": True, "user": _strip(self.state.user(uid))})
                return
            if self.path.startswith("/api/users/") and self.path.rstrip("/").endswith("/garmin-key"):
                uid = self._uid_from_suffix("/garmin-key")
                api_key = str(payload.get("api_key") or "")
                if not api_key:
                    self._json(400, {"ok": False, "error": "api_key required"})
                    return
                self.state.set_garmin_key(uid, api_key, str(payload.get("name") or ""))
                self._json(200, {"ok": True, "user": _strip(self.state.user(uid))})
                return
            self._json(404, {"ok": False, "error": "not found"})
        except Exception as e:
            log.error("ingest POST %s failed: %s", self.path, redact_safe(e))
            self._json(500, {"ok": False, "error": "internal"})

    def _uid_from_suffix(self, suffix: str) -> Optional[int]:
        head = self.path.rstrip("/")
        if head.endswith(suffix):
            head = head[: -len(suffix)]
        try:
            return int(head.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            return None

    def do_DELETE(self):  # noqa: N802
        try:
            if not self._authorized():
                self._json(401, {"ok": False, "error": "unauthorized"})
                return
            if self.path.startswith("/api/users/"):
                uid = self._uid()
                row = self.state.user(uid) if uid is not None else None
                if row is None:
                    self._json(404, {"ok": False, "error": "unknown user"})
                    return
                ride_ids = self.state.delete_user(uid)
                if self.photos_root is not None:
                    from . import photos as photos_mod
                    for rid in ride_ids:  # buffered files go with the rows
                        photos_mod.clear_ride(self.photos_root, rid)
                log.info("deleted user %s with %d ride(s)", uid, len(ride_ids))
                self._json(200, {"ok": True})
                return
            self._json(404, {"ok": False, "error": "not found"})
        except Exception as e:
            log.error("ingest DELETE %s failed: %s", self.path, redact_safe(e))
            self._json(500, {"ok": False, "error": "internal"})


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def redact_safe(e: Exception) -> str:
    """Import-late redact to keep the module importable without the package cycle risk."""
    from .redact import redact
    return redact(e)
