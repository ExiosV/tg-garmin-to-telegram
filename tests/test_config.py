import pytest

from tg_garmin_to_telegram.config import Config

ENV_VARS = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_BOT_API_BASE_URL",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
    "GMAIL_QUERY", "DATA_DIR", "POLL_SECONDS", "MAX_AGE_HOURS",
    "INGEST_PORT", "INGEST_TOKEN", "WEB_SESSION_SECRET", "OWNER_NAME",
    "PHOTO_DIR", "PHOTO_MAX_PER_RIDE", "PHOTO_MAX_BYTES",
]


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_on_empty_env(clean_env):
    cfg = Config.from_env()
    assert cfg.token == "" and cfg.channel == ""
    assert cfg.api_base == "https://api.telegram.org"
    assert cfg.query == "from:garmin.com to:garmin-telegram newer_than:2d"
    assert cfg.data_dir == "./data" and cfg.poll_seconds == 30
    assert cfg.max_age_hours == 12 and cfg.ingest_port == 8080
    assert cfg.ingest_token == "" and cfg.web_session_secret == ""
    assert cfg.photo_dir == "./data/photos"  # the photo buffer lives under DATA_DIR
    assert cfg.photo_max_per_ride == 10 and cfg.photo_max_bytes == 8 * 1024 * 1024
    assert cfg.owner == "the rider"


def test_env_values_override_defaults(clean_env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "-100999")
    monkeypatch.setenv("TELEGRAM_BOT_API_BASE_URL", "http://localhost:8081/")
    monkeypatch.setenv("POLL_SECONDS", "45")
    monkeypatch.setenv("MAX_AGE_HOURS", "6.5")
    monkeypatch.setenv("INGEST_PORT", "9090")
    monkeypatch.setenv("INGEST_TOKEN", "ingest-secret")
    monkeypatch.setenv("GMAIL_QUERY", "from:garmin.com newer_than:1d")
    monkeypatch.setenv("OWNER_NAME", "Igor")
    monkeypatch.setenv("PHOTO_DIR", "/var/lib/gtt/photos")
    monkeypatch.setenv("PHOTO_MAX_PER_RIDE", "5")
    monkeypatch.setenv("PHOTO_MAX_BYTES", "99")
    cfg = Config.from_env()
    assert cfg.token == "123:ABC" and cfg.channel == "-100999"
    assert cfg.api_base == "http://localhost:8081"  # trailing slash stripped
    assert cfg.poll_seconds == 45 and cfg.max_age_hours == 6.5 and cfg.ingest_port == 9090
    assert cfg.ingest_token == "ingest-secret"
    assert cfg.query == "from:garmin.com newer_than:1d" and cfg.owner == "Igor"
    assert cfg.photo_dir == "/var/lib/gtt/photos"
    assert cfg.photo_max_per_ride == 5 and cfg.photo_max_bytes == 99


def test_malformed_numbers_fall_back_to_defaults(clean_env, monkeypatch, caplog):
    monkeypatch.setenv("POLL_SECONDS", "thirty")
    monkeypatch.setenv("INGEST_PORT", "")
    monkeypatch.setenv("MAX_AGE_HOURS", "not-a-number")
    with caplog.at_level("WARNING"):
        cfg = Config.from_env()
    assert cfg.poll_seconds == 30 and cfg.max_age_hours == 12 and cfg.ingest_port == 8080
    assert any("POLL_SECONDS" in r.message for r in caplog.records)
