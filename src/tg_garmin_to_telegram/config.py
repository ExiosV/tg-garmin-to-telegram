import logging
import os
from dataclasses import dataclass

log = logging.getLogger("tg_garmin_to_telegram")


def _int_env(get, name: str, default: int) -> int:
    """Tolerant integer env var: empty = default, malformed = warning and default, never a crash."""
    raw = str(get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s=%r is not an integer; using default %s", name, raw, default)
        return default


def _float_env(get, name: str, default: float) -> float:
    raw = str(get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using default %s", name, raw, default)
        return default


@dataclass
class Config:
    token: str = ""          # the service bot (serves /start, diagnostics); users deliver with their own bot tokens
    channel: str = ""        # TELEGRAM_CHANNEL_ID: self-check only, NOT part of the multi-user delivery path
    api_base: str = "https://api.telegram.org"
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""  # the Gmail mailbox that receives Garmin LiveTrack mail
    query: str = "from:garmin.com to:garmin-telegram newer_than:2d"
    data_dir: str = "./data"
    poll_seconds: int = 30
    max_age_hours: float = 12  # mails older than this are not announced after an outage
    ingest_port: int = 8080    # ingest HTTP API port (the website and the dashboard read through it)
    ingest_token: str = ""     # cluster-internal bearer for the ingest API
    web_session_secret: str = ""  # placeholder: web onboarding will sign its sessions with it
    email_domain: str = "exiosv.com"  # the LiveTrack recipient domain: garmin-telegram+<token>@<domain>
    site_url: str = ""        # the website shown in the /start menu; empty hides the button
    photo_dir: str = ""       # PHOTO_DIR: photo buffer root; empty = DATA_DIR + "/photos" (photos.root)
    photo_max_per_ride: int = 30      # buffered photos accepted per ride; extras are refused
    photo_max_bytes: int = 8 * 1024 * 1024  # one photo bigger than this is refused
    owner: str = "the rider"

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(
            token=e("TELEGRAM_BOT_TOKEN", ""),
            channel=e("TELEGRAM_CHANNEL_ID", ""),
            api_base=e("TELEGRAM_BOT_API_BASE_URL", "https://api.telegram.org").rstrip("/"),
            client_id=e("GOOGLE_CLIENT_ID", ""),
            client_secret=e("GOOGLE_CLIENT_SECRET", ""),
            refresh_token=e("GOOGLE_REFRESH_TOKEN", ""),
            query=e("GMAIL_QUERY", "from:garmin.com to:garmin-telegram newer_than:2d"),
            data_dir=e("DATA_DIR", "./data"),
            poll_seconds=_int_env(e, "POLL_SECONDS", 30),
            max_age_hours=_float_env(e, "MAX_AGE_HOURS", 12),
            ingest_port=_int_env(e, "INGEST_PORT", 8080),
            ingest_token=e("INGEST_TOKEN", ""),
            web_session_secret=e("WEB_SESSION_SECRET", ""),
            email_domain=e("EMAIL_DOMAIN", "exiosv.com"),
            site_url=e("SITE_URL", ""),
            photo_dir=e("PHOTO_DIR", "") or os.path.join(e("DATA_DIR", "./data"), "photos"),
            photo_max_per_ride=_int_env(e, "PHOTO_MAX_PER_RIDE", 30),
            photo_max_bytes=_int_env(e, "PHOTO_MAX_BYTES", 8 * 1024 * 1024),
            owner=e("OWNER_NAME", "the rider"),
        )
