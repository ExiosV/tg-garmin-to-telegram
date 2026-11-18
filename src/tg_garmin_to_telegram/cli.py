import argparse
import logging
import sys
import time

from .config import Config
from .gmail import Gmail
from .redact import redact
from .state import State
from .telegram import Telegram

log = logging.getLogger("tg_garmin_to_telegram")


def poll_once(cfg, gmail, tg, state) -> int:
    """One ingest pass. Mail parsing and delivery are not implemented yet; this stub only keeps the
    process shape and proves the adapters wire up. Returns the number of handled mails."""
    # TODO(next iteration): gmail.list_ids(cfg.query) -> parse the LiveTrack mail -> match the user
    # by recipient/token -> announce the start in the user's own chat via MultiBot.
    return 0


def run_forever(cfg, gmail, tg, state) -> None:
    log.info("poll loop started: every %ss, query=%r (delivery not implemented yet)",
             cfg.poll_seconds, cfg.query)
    while True:
        started = time.time()
        try:
            poll_once(cfg, gmail, tg, state)
        except Exception as e:  # no raw traceback: exception text can contain a bot token in a URL
            log.error("poll pass failed: %s", redact(e))
        delay = max(1.0, cfg.poll_seconds - (time.time() - started))
        time.sleep(delay)


def _build_gmail(cfg):
    """Build the Gmail adapter; `check` must still report the bot when mailbox creds are missing."""
    try:
        return Gmail(cfg.client_id, cfg.client_secret, cfg.refresh_token)
    except Exception as e:
        log.error("gmail adapter unavailable: %s", redact(e))
        return None


def _check(cfg, tg, gmail) -> None:
    """Self-check of the service bot and the Gmail mailbox; never prints a token."""
    try:
        me = tg.whoami()
        print("bot:", me.get("username"), "| id:", me.get("id"))
    except Exception as e:
        print("bot: error:", redact(e))
    if cfg.channel:
        try:
            print("channel:", tg.get_chat_title(cfg.channel), "| bot status:", tg.my_status(cfg.channel))
        except Exception as e:
            print("channel: error:", redact(e))
        print("note: TELEGRAM_CHANNEL_ID is a self-check only; users deliver to their own chats")
    else:
        print("channel: not set (TELEGRAM_CHANNEL_ID is optional; self-check only)")
    if gmail is None:
        print("mailbox: error: adapter unavailable (see log)")
    else:
        try:
            print("mailbox:", gmail.email())
        except Exception as e:
            print("mailbox: error:", redact(e))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="tg_garmin_to_telegram")
    ap.add_argument("cmd", choices=["run", "once", "check"])
    cmd = ap.parse_args().cmd
    cfg = Config.from_env()
    tg = Telegram(cfg.token, cfg.api_base, cfg.channel)
    if cmd == "check":
        _check(cfg, tg, _build_gmail(cfg))
        return
    try:
        gmail = _build_gmail(cfg)
        if gmail is None:
            sys.exit(1)  # run/once cannot work without the mailbox; the reason is in the log
        state = State(cfg.data_dir)
        if cmd == "once":
            print("handled:", poll_once(cfg, gmail, tg, state))
            return
        run_forever(cfg, gmail, tg, state)
    except Exception as e:  # no raw traceback: exception text can contain the bot token in a URL
        log.error("fatal: %s", redact(e))
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
