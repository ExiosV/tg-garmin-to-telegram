"""Mail polling and delivery: LiveTrack mails land in the watched mailbox addressed to
garmin-telegram+<email_token>@exiosv.com; each start is announced in the recipient user's own
chat as their own bot ('own') or as the service bot ('our'). Reliability model is the donor's
(tg-garmin-livetrack): mark a mail seen only after the post succeeded, retry the marks that the
database refused, and treat the first pass as a backlog scan that posts nothing."""
import html
import logging
import time
from typing import Optional

from google_kit.errors import GoogleError
from tg_botkit.bot import TelegramError

from . import i18n
from .parse import header, livetrack_link, session_id, session_url
from .redact import redact

log = logging.getLogger("tg_garmin_to_telegram")

UPDATES_OFFSET_PREFIX = "tg_offset:"  # per-user Bot API offset in meta: tg_offset:<user_id>
# Bot API statuses that mean the delivery target itself is misconfigured (bot kicked or blocked,
# wrong chat id): retrying changes nothing, the mail is consumed and the user gets an event.
PERMANENT_SEND_STATUSES = {400, 403}


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _forwarded_from(msg: dict) -> Optional[tuple]:
    """(source chat id, source message id) of a forwarded message in Bot API 7.0+ or legacy shape."""
    origin = msg.get("forward_origin")
    if origin:
        chat = origin.get("chat") or {}
        return _as_int(chat.get("id")), origin.get("message_id")
    if msg.get("forward_from_chat"):
        return _as_int(msg["forward_from_chat"].get("id")), msg.get("forward_from_message_id")
    return None


def start_text(user: dict, link: str) -> str:
    """The announcement: rider name per the user's language plus the map link to the LiveTrack.
    The full link (token included) is the capability the recipient needs to watch the ride."""
    lang = user.get("lang")
    name = (user.get("garmin_name") or user.get("target_title")
            or i18n.text(lang, "default_name"))
    return i18n.text(lang, "ride_started", name=html.escape(str(name or "")),
                     link=html.escape(link))


def too_old(msg: dict, max_age_hours: float) -> bool:
    """A LiveTrack link from a long-finished ride is useless: after a long outage do not announce it."""
    ms = msg.get("internalDate")
    return bool(ms) and time.time() - int(ms) / 1000 > max_age_hours * 3600


def start_ts(msg: dict) -> int:
    """Ride start = the mail time the loop already uses; fall back to now if Gmail omitted it."""
    ms = msg.get("internalDate")
    return int(ms) // 1000 if ms else int(time.time())


def match_user(users: list, msg: dict) -> Optional[dict]:
    """The recipient user: the mail's To/Delivered-To carries garmin-telegram+<email_token>@.
    The Gmail query is only a pre-filter; this code-level check is the real gate, so a mail with
    no matching active user returns None and the caller skips it."""
    to = f"{header(msg, 'To')} {header(msg, 'Delivered-To')}".lower()
    for u in users:
        token = str(u.get("email_token") or "").strip().lower()
        if token and f"garmin-telegram+{token}@" in to:
            return u
    return None


def _pending_marks(state) -> dict:
    """Mails already sent but not yet marked, per State instance (i.e. per process).

    A restart loses this set, which is the documented at-most-one duplicate; within one process it
    stops run_forever from re-posting a ride every cycle while the database refuses writes."""
    pending = getattr(state, "_pending_marks", None)
    if pending is None:
        pending = {}
        state._pending_marks = pending
    return pending


def _retry_pending_marks(state, pending: dict) -> None:
    for mid, canonical in list(pending.items()):
        try:
            state.mark_seen(mid, canonical)
        except Exception as e:  # reads may work while writes fail; retry at the next cycle
            log.warning("could not mark mail %s as sent (will retry): %s", mid, redact(e))
            continue
        del pending[mid]


def poll_once(cfg, gmail, telegram_factory, state) -> int:
    """One mail-poll pass. Returns the number of announcements posted.

    Every mail is processed in its own guard: one broken recipient (a kicked bot, a wrong chat id,
    a deleted mail) retries alone and never blocks the other users' announcements."""
    pending = _pending_marks(state)
    _retry_pending_marks(state, pending)
    first = not state.initialized()
    # Arm the flag before the loop: if this pass dies midway, the next one must process the rest
    # as normal mails instead of silently marking the whole backlog seen a second time.
    state.set_initialized()
    posted = 0
    for mid in reversed(gmail.list_ids(cfg.query)):  # oldest first
        if mid in pending or state.mail_seen(mid):
            continue
        try:
            posted += _process_mail(cfg, gmail, telegram_factory, state, pending, mid, first)
        except Exception as e:  # transient (network, Google 5xx, rate limit): retry next pass;
            log.error("mail %s processing failed (will retry): %s", mid, redact(e))  # others proceed
    poll_discussions(telegram_factory, state)
    poll_rides(cfg, state)
    return posted


def _process_mail(cfg, gmail, telegram_factory, state, pending: dict, mid, first: bool) -> int:
    """One mail: fetch, match the recipient, announce. Returns 1 when a post went out.

    Transient failures raise (the pass retries this mail next cycle, other mails already went
    through this cycle); permanent ones (mail deleted, target misconfigured) consume the mail."""
    try:
        msg = gmail.get(mid)
    except GoogleError as e:
        if e.status in (404, 410):  # the mail vanished between listing and reading: consume it
            state.mark_seen(mid)
            log.warning("mail %s disappeared (deleted?), marked seen", mid)
            return 0
        raise  # transient Google-side failure: abort the pass, retry next cycle
    link = livetrack_link(msg)
    # Persist and log only the token-less URL: the token in the link is a capability to the live track.
    canonical = session_url(link) if link else None
    user = match_user(state.active_users(), msg)
    if user is None:
        # Unknown recipient: skip silently. Mark by id only, never by link: the same session may
        # arrive later addressed to an active user, and that mail must still be announced.
        state.mark_seen(mid)
        return 0
    if not link or first or too_old(msg, cfg.max_age_hours) or state.link_seen(canonical, user["user_id"]):
        # First run marks the backlog seen instead of spamming old rides; same for stale mail and
        # sessions this user has already been announced (per user: a shared Garmin account is
        # announced to every user who follows it).
        state.mark_seen(mid, canonical, user["user_id"])
        return 0
    token = user["bot_token"] if user.get("delivery_mode") == "own" else cfg.token
    bot = telegram_factory.bot_for(token)
    try:
        channel_msg_id = bot.send(start_text(user, link), chat_id=user["target_id"])
    except TelegramError as e:
        if e.status in PERMANENT_SEND_STATUSES:
            # The target itself is misconfigured (bot kicked/blocked, wrong chat id): consuming the
            # mail beats a 12-hour starvation of every other user; the user gets an event.
            state.mark_seen(mid, canonical, user["user_id"])
            state.add_event(user["user_id"], "delivery_failed",
                            {"status": e.status, "description": e.description, "mail": str(mid)})
            log.error("permanent delivery failure for user %s (HTTP %s), mail consumed: %s",
                      user["user_id"], e.status, redact(e.description))
            return 0
        raise  # transient (429, 5xx, network): leave unseen, retry next pass; other mails proceed
    posted = 1
    # Mark first: a bookkeeping failure below must never leave the mail unseen and re-post the ride.
    pending[mid] = canonical
    try:
        state.mark_seen(mid, canonical, user["user_id"])
        del pending[mid]
    except Exception as e:  # already public: never send it again in this process
        log.warning("could not mark mail %s as sent (will retry): %s", mid, redact(e))
    sid = session_id(link)
    log.info("announced session %s to user %s", sid, user["user_id"])
    if sid:
        try:
            state.start_ride(user["user_id"], sid, canonical, channel_msg_id, start_ts(msg))
        except Exception as e:  # the post is already public; losing the row is better than a loop
            log.error("ride bookkeeping failed for session %s: %s", sid, redact(e))
    return posted


def poll_discussions(telegram_factory, state) -> int:
    """Best-effort thread discovery for delivery_mode='own' bots: read each such bot's getUpdates
    once per pass and remember the auto-forwarded copy of the announcement (the thread root).
    Channels without a linked discussion never produce one: discuss_msg_id stays NULL. 'our'-mode
    users are skipped: the reg bot's getUpdates belongs to the onboarding iteration. Never raises."""
    linked = 0
    for user in state.active_users():
        if user.get("delivery_mode") != "own":
            continue
        if not state.unlinked_discussion_rides(user["user_id"]):
            continue  # nothing to link for this user: do not hit getUpdates at all
        try:
            linked += _poll_user_discussion(telegram_factory, state, user)
        except Exception as e:  # a broken Telegram read must not stop mail polling
            log.error("discussion updates failed for user %s: %s", user["user_id"], redact(e))
    return linked


def _poll_user_discussion(telegram_factory, state, user: dict, wait: int = 1) -> int:
    """One 'own' bot's getUpdates pass. The offset lives in meta as tg_offset:<user_id> so restarts
    do not replay updates. Returns the number of rides linked."""
    key = UPDATES_OFFSET_PREFIX + str(user["user_id"])
    offset = _as_int(state.get_meta(key, "0")) or 0
    bot = telegram_factory.bot_for(user["bot_token"])
    updates = bot.updates(offset, wait=wait, allowed=("message",))
    target = _as_int(user["target_id"])
    linked = 0
    for update in updates:
        offset = max(offset, _as_int(update.get("update_id")) or 0) + 1
        msg = update.get("message") or {}
        if not msg.get("is_automatic_forward"):
            continue
        source = _forwarded_from(msg)
        if not source or not source[1] or not msg.get("message_id"):
            log.debug("ignoring auto-forward for user %s: no source message", user["user_id"])
            continue
        if source[0] != target:
            continue
        ride = state.ride_by_channel_msg(user["user_id"], source[1])
        if not ride:
            # Our own post, but no ride row: bookkeeping failed or the post predates the feature.
            log.warning("auto-forward of announcement %s has no matching ride (user %s); "
                        "the thread link is lost for that ride", source[1], user["user_id"])
            continue
        if state.set_discuss_msg(ride["session_id"], msg["message_id"]):
            linked += 1
            log.info("linked ride %s: announcement %s -> discussion message %s",
                     ride["session_id"], source[1], msg["message_id"])
    if updates:
        state.set_meta(key, str(offset))
    return linked


def poll_rides(cfg, state) -> None:
    """F1b/F3: close started rides once their Garmin activity is known, then photo albums.
    Not implemented yet: the ingest API and the ride-end matcher come in later iterations."""
    return None


def run_forever(cfg, gmail, telegram_factory, state) -> None:
    log.info("poll loop started: every %ss, query=%r", cfg.poll_seconds, cfg.query)
    while True:
        started = time.time()
        try:
            poll_once(cfg, gmail, telegram_factory, state)
        except Exception as e:  # no raw traceback: exception text can contain a bot token in a URL
            log.error("poll pass failed: %s", redact(e))
        delay = max(1.0, cfg.poll_seconds - (time.time() - started))
        time.sleep(delay)
