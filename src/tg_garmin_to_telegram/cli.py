import argparse
import logging
import sys
import threading
import time

from .config import Config
from .gmail import Gmail
from .redact import redact
from . import onboarding
from .runner import poll_once, run_forever, set_chat_member_handler
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
            onboarding.poll_once(cfg, state, deliveries)
            return
        if cfg.ingest_token:
            _start_ingest(cfg, state)
        else:
            log.warning("INGEST_TOKEN is empty: the ingest API stays off")
        from . import onboarding
        set_chat_member_handler(lambda state, update: onboarding.handle_own_chat_member(
            cfg, state, deliveries, update))
        threading.Thread(target=_onboarding_loop, args=(cfg, state, deliveries),
                         name="onboarding", daemon=True).start()
        run_forever(cfg, gmail, deliveries, state)  # the mail loop owns the main thread
    except Exception as e:  # no raw traceback: exception text can contain the bot token in a URL
        log.error("fatal: %s", redact(e))
        sys.exit(1)


def _start_ingest(cfg, state) -> None:
    """The ingest HTTP server as a daemon thread; a boot failure is fatal (the dashboard and the
    website would silently lose their data source otherwise)."""
    from . import ingest
    httpd = ingest.serve(cfg.ingest_port, cfg.ingest_token, state)
    threading.Thread(target=httpd.serve_forever, name="ingest", daemon=True).start()


def _onboarding_loop(cfg, state, deliveries) -> None:
    """The service bot's getUpdates loop (onboarding owns this token's updates)."""
    from . import onboarding
    while True:
        started = time.time()
        try:
            onboarding.poll_once(cfg, state, deliveries)
        except Exception as e:  # keep the loop alive; the mail loop is a separate thread
            log.error("onboarding pass failed: %s", redact(e))
        time.sleep(max(1.0, cfg.poll_seconds - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
