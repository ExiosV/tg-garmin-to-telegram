# tg-garmin-to-telegram

Multi-user Telegram service: when a user's Garmin activity starts (a LiveTrack email lands in the
watched Gmail mailbox), the service announces the start in that user's own Telegram group or
channel — sent as the user's own bot.

**Alpha**: automatic adding of ride photos to the group or to the discussion thread of channel
posts is available — the user sends the photos to the bot during the ride, and the album appears
in the thread after the ride ends.

## Run

```sh
cp .env.example .env   # fill in the values
python3 -m venv .venv && .venv/bin/pip install -e .[dev]
.venv/bin/python -m tg_garmin_to_telegram check   # self-check: bot, optional channel, mailbox
.venv/bin/python -m tg_garmin_to_telegram run     # poll loop (delivery comes in the next iteration)
.venv/bin/python -m tg_garmin_to_telegram once    # single pass
```

Docker: `docker build -t tg-garmin-to-telegram .` then `docker run --env-file .env tg-garmin-to-telegram run`.

## Config

All configuration is environment-based; see `.env.example` (secrets are provisioned via Bitwarden-backed
ExternalSecrets in k8s). `TELEGRAM_CHANNEL_ID` exists for the `check` self-check only — delivery targets
are per user and live in the state database.

## Layout

- `src/tg_garmin_to_telegram/` — the package: config, SQLite state, redaction, Telegram and Gmail
  adapters, Garmin Personal API client, message catalog (RU/EN), CLI (`run|once|check`).
- `tests/` — pytest, fakes over the adapters, real SQLite on `tmp_path`.

License: Apache-2.0.
