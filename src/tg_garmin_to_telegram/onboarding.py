"""The onboarding loop: the service bot's own getUpdates (its offset lives in meta as `onb_offset`).

Walks a user from first contact to an active delivery target without any web UI:
- /start explains the flow and shows the user's personal LiveTrack address
  (garmin-telegram+<email_token>@<domain>) plus a menu of buttons;
- «My own bot»: the user sends their bot token as a message (deleted immediately after reading),
  then adds that bot as an administrator to their group or channel — the delivery loop's
  getUpdates sees the my_chat_member update and routes it here (set_chat_member_handler);
- «Service bot in my group»: the user adds @garmin_start_bot as an administrator — the same
  my_chat_member path on the service bot;
- «Garmin key»: the user sends their Personal API key as a message (deleted immediately); it
  drives the ride-end matching later.

Every update is processed in its own guard and the offset advances even for unhandled updates,
so one bad update never stops or replays the onboarding. The mail loop and this loop own
DIFFERENT bots: the mail loop never consumes the service bot's getUpdates, this loop never
consumes a user bot's getUpdates."""
from __future__ import annotations

import html
import json
import logging
import secrets
import time
from typing import Optional

import requests

from . import i18n
from .garmin_api import GarminApiClient, GarminApiError, is_auth_error
from .redact import redact

log = logging.getLogger("tg_garmin_to_telegram")

ONB_OFFSET = "onb_offset"
PENDING_TTL_S = 600  # an unfinished dialog is abandoned after this long

STAGE_OWN_TOKEN = "onb_own_token"
STAGE_OWN_CHAT = "onb_own_chat"
STAGE_OWN_CHAT_CONFIRM = "onb_own_chat_confirm"
STAGE_GARMIN_KEY = "onb_garmin_key"

def _pending(state, uid: int) -> Optional[dict]:
    raw = state.get_meta(f"pending:{uid}")
    if not raw:
        return None
    try:
        p = json.loads(raw)
    except ValueError:
        return None
    if time.time() - p.get("ts", 0) > PENDING_TTL_S:
        state.delete_meta(f"pending:{uid}")
        return None
    return p


def _pending_set(state, uid: int, stage: str, **data) -> None:
    state.set_meta(f"pending:{uid}", json.dumps({"stage": stage, "ts": int(time.time()), **data}))


def _pending_pop(state, uid: int) -> Optional[dict]:
    p = _pending(state, uid)
    if p:
        state.delete_meta(f"pending:{uid}")
    return p


def _kbd(rows: list) -> str:
    """Inline keyboard markup for tg_botkit's raw call()."""
    return json.dumps({"inline_keyboard": [
        [{"text": t, "callback_data": d} for (t, d) in row] for row in rows
    ]})


def ensure_email_link(cfg, state, uid: int) -> dict:
    """First contact: make sure the row exists and carries its personal LiveTrack address."""
    u = state.user(uid)
    if u is None or not u.get("email_token"):
        token = secrets.token_urlsafe(12)
        addr = f"garmin-telegram+{token}@{cfg.email_domain}"
        state.touch_user(uid, chat_id=uid)
        state.set_email_link(uid, token, addr)
        u = state.user(uid)
    return u


def _send(bot, chat_id, text: str, kbd: Optional[list] = None) -> None:
    extra = {"reply_markup": _kbd(kbd)} if kbd else {}
    try:
        bot.call("sendMessage", text=text, parse_mode="HTML", chat_id=chat_id,
                 disable_web_page_preview=True, **extra)
    except Exception as e:  # an HTML rejection falls back to plain text inside botkit; a failure
        log.error("onboarding send to %s failed: %s", chat_id, redact(e))  # is logged, never raised


def _menu(user: dict, cfg) -> list:
    rows = [
        [(i18n.text(user.get("lang"), "menu_address"), "onb:address")],
        [(i18n.text(user.get("lang"), "menu_own_bot"), "onb:own_bot"),
         (i18n.text(user.get("lang"), "menu_service_bot"), "onb:service_bot")],
        [(i18n.text(user.get("lang"), "menu_garmin_key"), "onb:garmin_key"),
         (i18n.text(user.get("lang"), "menu_status"), "onb:status")],
    ]
    if cfg.site_url:
        rows.append([(i18n.text(user.get("lang"), "menu_site"), "onb:site")])
    return rows


def _status_text(state, user: dict) -> str:
    lang = user.get("lang")
    mode = user.get("delivery_mode") or ""
    mode_text = i18n.text(lang, f"mode_{mode}") if mode in ("own", "our") else i18n.text(lang, "not_set")
    target = user.get("target_title") or i18n.text(lang, "not_set")
    garmin = i18n.text(lang, "no_garmin") if not user.get("garmin_api_key") else user.get("garmin_name") or "✓"
    state_key = user.get("state") or "setup"
    state_text = i18n.text(lang, f"state_{state_key}") if state_key in ("setup", "active") else state_key
    return i18n.text(lang, "status", state=state_text, mode=mode_text, target=target,
                     email_addr=user.get("email_addr") or i18n.text(lang, "not_set"), garmin=garmin)


def _finish_target(state, user: dict, cfg, bot, chat: dict, mode: str) -> None:
    lang = user.get("lang")
    state.set_target(user["user_id"], mode, chat.get("type", "group"),
                     chat.get("id"), chat.get("title") or "", user.get("bot_token") or "")
    state.set_user_state(user["user_id"], "active")
    title = html.escape(str(chat.get("title") or ""))
    _send(bot, user["user_id"], i18n.text(lang, "target_set", title=title))
    _send(bot, chat.get("id"), i18n.text(lang, "test_announce"))
    state.add_event(user["user_id"], "target_set",
                    {"mode": mode, "target_id": chat.get("id"), "title": chat.get("title")})


def handle_message(cfg, state, bots, msg: dict) -> None:
    """One text/command message from a user in the service bot's chat."""
    uid = _as_uid((msg.get("from") or {}).get("id"))
    if uid is None:
        return
    user = ensure_email_link(cfg, state, uid)
    lang = user.get("lang")
    text = (msg.get("text") or "").strip()
    bot = bots.bot_for(cfg.token)

    if text.startswith("/start"):
        _send(bot, uid, i18n.text(lang, "start", code=msg.get("from", {}).get("language_code")),
              kbd=_menu(user, cfg))
        return
    if text.startswith("/lang"):
        parts = text.split()
        if len(parts) > 1 and parts[1].lower() in (i18n.RU, i18n.EN):
            lang = parts[1].lower()
            state.set_lang(uid, lang)
            _send(bot, uid, i18n.text(lang, "lang_set", lang="RU" if lang == i18n.RU else "EN"))
        else:
            _send(bot, uid, i18n.text(lang, "lang_show", lang="RU" if lang == i18n.RU else "EN"))
        return
    if text.startswith("/status"):
        _send(bot, uid, _status_text(state, user))
        return
    if text.startswith("/cancel"):
        if _pending_pop(state, uid):
            _send(bot, uid, i18n.text(lang, "dialog_cancelled"))
        else:
            _send(bot, uid, i18n.text(lang, "unknown"))
        return
    if text.startswith("/help"):
        _send(bot, uid, i18n.text(lang, "help", code=msg.get("from", {}).get("language_code")))
        return

    pending = _pending(state, uid)
    if pending and pending.get("stage") == STAGE_OWN_TOKEN:
        _pending_pop(state, uid)
        try:
            bot.delete(msg.get("message_id"), chat_id=uid)  # the token leaves the chat at once
        except Exception as e:
            log.warning("could not delete the token message: %s", redact(e))
        try:
            bot_info = bots.bot_for(text).whoami()
        except Exception as e:
            _send(bot, uid, i18n.text(lang, "own_bot_token_bad", error=redact(str(e))))
            return
        state.set_own_bot_token(uid, text)
        state.add_event(uid, "own_bot_linked", {"bot": bot_info.get("username")})
        _send(bot, uid, i18n.text(lang, "own_bot_token_ok", bot_name=bot_info.get("username")))
        return
    if pending and pending.get("stage") == STAGE_GARMIN_KEY:
        _pending_pop(state, uid)
        try:
            bot.delete(msg.get("message_id"), chat_id=uid)  # the key leaves the chat at once
        except Exception as e:
            log.warning("could not delete the key message: %s", redact(e))
        try:
            acts = GarminApiClient(text).recent_activities(1)
        except GarminApiError as e:
            detail = "auth" if is_auth_error(e) else f"HTTP {e.status}"
            _send(bot, uid, i18n.text(lang, "garmin_key_bad", error=detail))
            return
        except (requests.RequestException, OSError) as e:
            log.warning("garmin key check unreachable: %s", redact(e))
            _send(bot, uid, i18n.text(lang, "garmin_key_bad", error="unreachable"))
            return
        name = ""
        if acts:
            name = (acts[0].get("owner_name") or acts[0].get("activityName") or "")
        state.set_garmin_key(uid, text, name)
        _send(bot, uid, i18n.text(lang, "garmin_key_ok", name=html.escape(str(name or "✓"))))
        return

    # any other text with no pending dialog: the help hint keeps the flow discoverable
    _send(bot, uid, i18n.text(lang, "unknown", code=msg.get("from", {}).get("language_code")))


def handle_callback(cfg, state, bots, cq: dict) -> None:
    """One inline-button press. Data: onb:address, onb:own_bot, onb:service_bot, onb:garmin_key,
    onb:status, onb:site, onb:chat_yes."""
    uid = _as_uid((cq.get("from") or {}).get("id"))
    if uid is None:
        return
    user = ensure_email_link(cfg, state, uid)
    lang = user.get("lang")
    data = cq.get("data") or ""
    bot = bots.bot_for(cfg.token)
    if data == "onb:address":
        _send(bot, uid, i18n.text(lang, "address", email_addr=user.get("email_addr")))
    elif data == "onb:own_bot":
        _pending_set(state, uid, STAGE_OWN_TOKEN)
        _send(bot, uid, i18n.text(lang, "own_bot_guide"))
    elif data == "onb:garmin_key":
        _pending_set(state, uid, STAGE_GARMIN_KEY)
        _send(bot, uid, i18n.text(lang, "garmin_key_guide"))
    elif data == "onb:service_bot":
        _send(bot, uid, i18n.text(lang, "service_bot_guide"))
    elif data == "onb:status":
        _send(bot, uid, _status_text(state, user))
    elif data == "onb:site":
        if cfg.site_url:
            _send(bot, uid, cfg.site_url)
    elif data == "onb:chat_yes":
        pending = _pending_pop(state, uid)
        if pending and pending.get("stage") == STAGE_OWN_CHAT_CONFIRM:
            _finish_target(state, user, cfg, bot, pending.get("chat") or {}, pending.get("mode", "own"))


def handle_own_chat_member(cfg, state, bots, update: dict) -> None:
    """A my_chat_member update on a USER's own bot (routed by the delivery loop): the bot was
    added to a chat — offer the confirmation for that chat. delivery_mode stays 'own'."""
    mcm = update.get("my_chat_member") or {}
    chat = mcm.get("chat") or {}
    status = ((mcm.get("new_chat_member") or {}).get("status")) or ""
    uid = _as_uid((mcm.get("from") or {}).get("id"))
    if uid is None or chat.get("type") not in ("group", "supergroup", "channel"):
        return
    if status not in ("administrator", "member"):
        return  # removed/left: nothing to confirm
    user = state.user(uid)
    if user is None or (user.get("state") == "active" and user.get("target_id")):
        return  # already delivering to a target: do not re-offer
    if not user.get("bot_token"):
        return  # the own-bot token is not linked yet: nothing to confirm
    bot = bots.bot_for(cfg.token)
    lang = user.get("lang")
    kind = i18n.text(lang, "kind_channel") if chat.get("type") == "channel" else \
        i18n.text(lang, "kind_group")
    _pending_set(state, uid, STAGE_OWN_CHAT_CONFIRM, mode="own", chat=chat)
    title = html.escape(str(chat.get("title") or ""))
    _send(bots.bot_for(cfg.token), uid,
          i18n.text(lang, "chat_confirm", title=title, kind=kind),
          kbd=[[(i18n.text(lang, "chat_confirm_yes"), "onb:chat_yes")]])


def handle_service_chat_member(cfg, state, bots, update: dict) -> None:
    """A my_chat_member update on the SERVICE bot: someone added @garmin_start_bot — our-mode."""
    mcm = update.get("my_chat_member") or {}
    chat = mcm.get("chat") or {}
    status = ((mcm.get("new_chat_member") or {}).get("status")) or ""
    uid = _as_uid((mcm.get("from") or {}).get("id"))
    if uid is None or chat.get("type") not in ("group", "supergroup", "channel"):
        return
    if status not in ("administrator", "member"):
        return
    user = ensure_email_link(cfg, state, uid)
    if user.get("state") == "active" and user.get("target_id"):
        return
    bot = bots.bot_for(cfg.token)
    lang = user.get("lang")
    kind = i18n.text(lang, "kind_channel") if chat.get("type") == "channel" else \
        i18n.text(lang, "kind_group")
    _pending_set(state, uid, STAGE_OWN_CHAT_CONFIRM, mode="our", chat=chat)
    title = html.escape(str(chat.get("title") or ""))
    _send(bot, uid,
          i18n.text(lang, "chat_confirm", title=title, kind=kind),
          kbd=[[(i18n.text(lang, "chat_confirm_yes"), "onb:chat_yes")]])


def _as_uid(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def poll_once(cfg, state, bots) -> None:
    """One service-bot getUpdates pass. Never raises: onboarding failures must not stop the
    mail loop, and the offset advances even across unhandled updates."""
    offset = int(state.get_meta(ONB_OFFSET, "0") or 0)
    bot = bots.bot_for(cfg.token)
    try:
        updates = bot.updates(offset, wait=25,
                              allowed=("message", "callback_query", "my_chat_member"))
    except Exception as e:  # Telegram unreachable or another consumer: retry next pass
        log.warning("onboarding getUpdates failed: %s", redact(e))
        return
    for update in updates:
        offset = max(offset, _as_int(update.get("update_id")) or 0) + 1
        try:
            if "message" in update:
                handle_message(cfg, state, bots, update["message"])
            elif "callback_query" in update:
                handle_callback(cfg, state, bots, update["callback_query"])
            elif "my_chat_member" in update:
                handle_service_chat_member(cfg, state, bots, update)
        except Exception as e:
            log.error("onboarding update %s failed: %s", update.get("update_id"), redact(e))
    if updates:
        state.set_meta(ONB_OFFSET, str(offset))


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
