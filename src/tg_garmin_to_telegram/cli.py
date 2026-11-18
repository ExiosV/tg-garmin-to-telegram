import argparse
import logging
import sys

from .config import Config
from .gmail import Gmail
from .redact import redact
from .runner import poll_once, run_forever
from .state import State
from .telegram import MultiBot, Telegram

log = logging.getLogger("tg_garmin_to_telegram")


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
        # Deliveries go out as the user's own bot ('own') or as the service bot ('our'): the
        # MultiBot factory owns those tokens, the Telegram service bot above is diagnostics only.
        deliveries = MultiBot(cfg.api_base)
        if cmd == "once":
            print("handled:", poll_once(cfg, gmail, deliveries, state))
            return
        run_forever(cfg, gmail, deliveries, state)
    except Exception as e:  # no raw traceback: exception text can contain the bot token in a URL
        log.error("fatal: %s", redact(e))
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
