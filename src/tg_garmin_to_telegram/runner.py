"""Mail polling and delivery: LiveTrack mails land in the watched mailbox addressed to
garmin-telegram+<email_token>@exiosv.com; each start is announced in the recipient user's own
chat as their own bot ('own') or as the service bot ('our'). Reliability model is the donor's
(tg-garmin-livetrack): mark a mail seen only after the post succeeded, retry the marks that the
database refused, and treat the first pass as a backlog scan that posts nothing.

Ride end and photos (F3): after the mail pass, poll_rides matches every started ride against the
user's recent Garmin activities (rate-limited, per-user key) to learn the exact end, then
publishes the buffered photos as one album into the ride's discussion thread (or the bare
ride-end summary when nothing was buffered)."""
import html
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from google_kit.errors import GoogleError
from tg_botkit.bot import TelegramError

from . import i18n
from . import photos as photos_mod
from .garmin_api import GarminApiClient, is_auth_error
from .parse import header, livetrack_link, session_id, session_url
from .redact import redact

log = logging.getLogger("tg_garmin_to_telegram")

UPDATES_OFFSET_PREFIX = "tg_offset:"  # per-user Bot API offset in meta: tg_offset:<user_id>
CHAT_MEMBER_HANDLER = None  # set by cli wiring: user bots' my_chat_member updates route to onboarding

# Ride-end matching (ported from the tg-garmin-livetrack donor, healthsync.py): the LiveTrack may
# start hours into the real ride, so the activity start can sit well before the announced start.
MATCH_BACK_S = 6 * 3600      # an activity started up to this long before the LiveTrack still matches
MATCH_FORWARD_S = 3600       # device vs mail clock skew in the other direction
MAX_RIDE_S = 24 * 3600       # longer than this is not a bike ride: do not match
ACTIVITY_URL = "https://connect.garmin.com/modern/activity/{activity_id}"

RIDE_CHECK_MIN_S = 10 * 60   # one Garmin check per user per window: be kind to the Personal API
RIDE_CHECK_PREFIX = "gride_check:"  # meta key: the last check time per user
PHOTO_ALBUM_MAX = 10         # one album keeps at most the first 10 buffered photos (by arrival)


def set_chat_member_handler(fn) -> None:
    """Register the onboarding handler for my_chat_member updates arriving on user bots (the bot
    was added to a chat — the onboarding offers the confirmation). Never raises from the mail loop."""
    global CHAT_MEMBER_HANDLER
    CHAT_MEMBER_HANDLER = fn
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
    for mid, (canonical, user_id) in list(pending.items()):
        try:
            state.mark_seen(mid, canonical, user_id)
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
    poll_rides(cfg, state, telegram_factory)
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
                            {"status": e.status, "description": redact(e.description), "mail": str(mid)})
            log.error("permanent delivery failure for user %s (HTTP %s), mail consumed: %s",
                      user["user_id"], e.status, redact(e.description))
            return 0
        raise  # transient (429, 5xx, network): leave unseen, retry next pass; other mails proceed
    posted = 1
    # Mark first: a bookkeeping failure below must never leave the mail unseen and re-post the ride.
    pending[mid] = (canonical, user["user_id"])
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
    """Best-effort per-user bot getUpdates passes, never raising:
    - active 'own'-mode users: thread discovery (the auto-forwarded copy of the announcement);
    - setup 'own'-mode users: the my_chat_member watch that powers the own-bot chat confirmation
      (a new user is not active yet — their bot's updates would otherwise never be read and the
      promised "I will see the chat myself" would never fire).
    'our'-mode users are skipped everywhere: the reg bot's getUpdates belongs to the onboarding
    loop."""
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
    for user in state.setup_own_users():
        try:
            _poll_setup_user_chat_member(telegram_factory, state, user)
        except Exception as e:
            log.error("chat-member watch failed for user %s: %s", user["user_id"], redact(e))
    return linked


def _poll_setup_user_chat_member(telegram_factory, state, user: dict, wait: int = 1) -> None:
    """One setup user's bot getUpdates pass watching for my_chat_member only. The onboarding
    handler consumes it (the chat confirmation); the offset lives in meta like the discovery one."""
    key = UPDATES_OFFSET_PREFIX + str(user["user_id"])
    offset = _as_int(state.get_meta(key, "0")) or 0
    bot = telegram_factory.bot_for(user["bot_token"])
    updates = bot.updates(offset, wait=wait, allowed=("my_chat_member",))
    for update in updates:
        offset = max(offset, _as_int(update.get("update_id")) or 0) + 1
        if "my_chat_member" in update and CHAT_MEMBER_HANDLER is not None:
            try:
                CHAT_MEMBER_HANDLER(state, update)
            except Exception as e:  # onboarding failures never break the watch
                log.error("chat-member handling failed for user %s: %s", user["user_id"], redact(e))
    if updates:
        state.set_meta(key, str(offset))


def _poll_user_discussion(telegram_factory, state, user: dict, wait: int = 1) -> int:
    """One 'own' bot's getUpdates pass. The offset lives in meta as tg_offset:<user_id> so restarts
    do not replay updates. Returns the number of rides linked."""
    key = UPDATES_OFFSET_PREFIX + str(user["user_id"])
    offset = _as_int(state.get_meta(key, "0")) or 0
    bot = telegram_factory.bot_for(user["bot_token"])
    updates = bot.updates(offset, wait=wait, allowed=("message", "my_chat_member"))
    target = _as_int(user["target_id"])
    linked = 0
    for update in updates:
        offset = max(offset, _as_int(update.get("update_id")) or 0) + 1
        if "my_chat_member" in update and CHAT_MEMBER_HANDLER is not None:
            try:
                CHAT_MEMBER_HANDLER(state, update)
            except Exception as e:  # onboarding failures never break thread discovery
                log.error("chat-member handling failed for user %s: %s", user["user_id"], redact(e))
            continue
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
        if state.set_discuss_msg(user["user_id"], ride["session_id"], msg["message_id"],
                                 discuss_chat_id=msg.get("chat", {}).get("id")):
            linked += 1
            log.info("linked ride %s: announcement %s -> discussion message %s",
                     ride["session_id"], source[1], msg["message_id"])
    if updates:
        state.set_meta(key, str(offset))
    return linked


def poll_rides(cfg, state, telegram_factory) -> None:
    """F3: close started rides once their Garmin activity is known, then publish the buffered
    photo albums. Best-effort per user: a broken Garmin key or a failed send never stops mail
    polling."""
    try:
        _match_ride_ends(state)
    except Exception as e:
        log.error("ride-end matching failed: %s", redact(e))
    try:
        _publish_ended_rides(cfg, state, telegram_factory)
    except Exception as e:
        log.error("ride publishing failed: %s", redact(e))


# -- ride-end matching (donor: tg-garmin-livetrack healthsync.py) ----------------------------------------------

def _activity_start(a: dict) -> Optional[int]:
    """The activity start as a UTC epoch: start_gmt/startTimeGMT (already GMT), else the local
    wall time minus utc_offset_min when the offset is known (best effort: parsed as UTC)."""
    for k in ("start_gmt", "startTimeGMT"):
        v = a.get(k)
        if v:
            try:
                return int(datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                pass
    for k in ("start", "startTimeLocal"):
        v = a.get(k)
        if v:
            try:
                ts = int(datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                continue
            offset = a.get("utc_offset_min")
            return ts - int(offset) * 60 if offset is not None else ts
    return None


def _activity_elapsed(a: dict) -> int:
    """The elapsed seconds under either naming (health-sync donor shape or the Garmin list)."""
    for k in ("elapsed_s", "elapsedDuration", "duration_s", "duration"):
        v = a.get(k)
        if v:
            try:
                return int(float(v))
            except (TypeError, ValueError):
                continue
    return 0


def activity_interval(a: dict) -> Optional[tuple]:
    """(start_epoch, end_epoch) of one activity; None when the start cannot be derived. The end
    comes from end_gmt when present, else start + elapsed (the donor's math)."""
    start = _activity_start(a)
    if start is None:
        return None
    end = None
    end_gmt = a.get("end_gmt")
    if end_gmt:
        try:
            end = int(datetime.fromisoformat(str(end_gmt)).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            end = None
    if end is None:
        end = start + _activity_elapsed(a)
    return start, end


def match_activity(activities: list, start_ts: int) -> Optional[dict]:
    """The activity whose [start, end] interval contains this LiveTrack session's start, None when
    there is none. ANY tracked activity type matches (the donor's ride was once saved as
    'walking' — the interval containment is the discriminator, not the type): an activity must
    have started at most MATCH_BACK_S before the LiveTrack began and not have ended when it began.
    Among (pathological) overlapping matches the earliest-ending one wins — the activity in
    progress, not a subsequent one."""
    best = best_end = None
    for a in activities:
        interval = activity_interval(a)
        if interval is None:
            continue
        start, end = interval
        if end - start > MAX_RIDE_S:
            continue
        if start_ts - MATCH_BACK_S <= start <= start_ts + MATCH_FORWARD_S and end >= start_ts:
            if best is None or end < best_end:
                best, best_end = a, end
    return best


def activity_end(a: dict) -> Optional[int]:
    """The end epoch of a matched activity; None when its interval cannot be derived."""
    interval = activity_interval(a)
    return interval[1] if interval else None


def activity_distance_m(a: dict) -> Optional[float]:
    """The activity distance in meters under either naming; None when absent."""
    for k in ("distance", "distance_m"):
        v = a.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def activity_id(a: dict) -> str:
    return str(a.get("activityId") or a.get("id") or "")


def _match_ride_ends(state) -> None:
    """Per active user with a Garmin key and started rides: one rate-limited recent_activities(30)
    call, then interval-match every started ride. A match closes the ride (state 'ended') and
    stores the activity id; the publish step of the same pass sends the album."""
    for user in state.active_users():
        uid = user["user_id"]
        if not user.get("garmin_api_key"):
            continue
        started = state.user_rides(uid, state="started")
        if not started:
            continue
        key = RIDE_CHECK_PREFIX + str(uid)
        now = int(time.time())
        if now - (_as_int(state.get_meta(key, "0")) or 0) < RIDE_CHECK_MIN_S:
            continue
        state.set_meta(key, str(now))  # burn the window first: one try per window, failures included
        try:
            activities = GarminApiClient(user["garmin_api_key"]).recent_activities(30)
        except Exception as e:
            if is_auth_error(e):
                # The key died (expired/revoked): one event for the user, no spam — the window
                # above gates when the next check happens, and the event is not repeated.
                state.add_event(uid, "garmin_key_dead", {"error": redact(str(e))})
                log.error("garmin key rejected for user %s: %s", uid, redact(e))
            else:
                log.warning("garmin activity check failed for user %s: %s", uid, redact(e))
            continue
        for ride in started:
            act = match_activity(activities, ride["started_ts"] or 0)
            if act is None:
                continue
            end = activity_end(act)
            if end is None:
                continue
            state.set_ride_end(ride["session_id"], end, distance_m=activity_distance_m(act))
            aid = activity_id(act)
            if aid:
                state.set_activity(uid, ride["session_id"], aid)
            log.info("ride %s ended via Garmin activity %s", ride["session_id"], aid or "?")


# -- publish: album into the thread (or the summary when nothing was buffered) ---------------------------------

def _publish_ended_rides(cfg, state, telegram_factory) -> None:
    """Every 'ended' ride (matched, not yet published) gets its album or its summary. One broken
    ride retries alone; the rest publish in the same pass."""
    for user in state.active_users():
        for ride in state.user_rides(user["user_id"], state="ended", limit=20):
            try:
                _publish_ride(cfg, state, telegram_factory, user, ride)
            except TelegramError as e:
                log.warning("publish deferred for ride %s (HTTP %s): %s",
                            ride["session_id"], e.status, redact(e.description))
            except Exception as e:
                log.error("publish failed for ride %s: %s", ride["session_id"], redact(e))


def _ride_caption(user: dict, ride: dict) -> str:
    """The ride-end summary: rider name, km and the Garmin activity link (the announcement's
    token-less LiveTrack link stands in when no activity id was matched)."""
    lang = user.get("lang")
    name = (user.get("garmin_name") or user.get("target_title")
            or i18n.text(lang, "default_name"))
    km = f"{ride['distance_m'] / 1000:.1f}" if ride.get("distance_m") else "—"
    link = (ACTIVITY_URL.format(activity_id=ride["activity_id"])
            if ride.get("activity_id") else (ride.get("live_url") or ""))
    return i18n.text(lang, "ride_ended", name=html.escape(str(name or "")), km=km,
                     link=html.escape(link))


def _publish_ride(cfg, state, telegram_factory, user: dict, ride: dict) -> None:
    """One ended ride: the buffered photos go out as one media group with the summary as caption,
    else the summary goes as a text message. The thread (discuss_msg_id) is answered when the
    discussion chat is known, else the target chat gets the post. Success moves the ride to
    'posted'/'nophotos'; a failure keeps 'ended' and the next pass retries (at most one duplicate
    summary when a send succeeded and the state write did not — the mail loop's trade-off)."""
    sid = ride["session_id"]
    token = user["bot_token"] if user.get("delivery_mode") == "own" else cfg.token
    bot = telegram_factory.bot_for(token)
    chat = int(ride["discuss_chat_id"]) if ride.get("discuss_chat_id") else int(user["target_id"])
    reply_to = (int(ride["discuss_msg_id"])
                if ride.get("discuss_chat_id") and ride.get("discuss_msg_id") else None)
    caption = _ride_caption(user, ride)
    photos = []
    if user.get("photos_alpha", 1):
        photos = photos_mod.load(photos_mod.root(cfg), state.ride_photos(ride["ride_id"]),
                                 PHOTO_ALBUM_MAX)
    if photos:
        bot.group("photo", photos, caption=caption, chat_id=chat, reply_to=reply_to)
        n = len(photos)
        state.clear_photos(ride["ride_id"])
        photos_mod.clear_ride(photos_mod.root(cfg), ride["ride_id"])
        state.set_ride_published(sid)
        log.info("ride %s published: %d photo(s) into chat %s", sid, n, chat)
        _album_note(cfg, telegram_factory, user, n)
    else:
        bot.send(caption, chat_id=chat, reply_to=reply_to)
        if state.ride_photos(ride["ride_id"]):  # alpha off or files gone: nothing will publish
            state.clear_photos(ride["ride_id"])
            photos_mod.clear_ride(photos_mod.root(cfg), ride["ride_id"])
        state.set_ride_nophotos(sid)
        log.info("ride %s ended with no photos: summary sent to chat %s", sid, chat)


def _album_note(cfg, telegram_factory, user: dict, n: int) -> None:
    """A one-line confirmation to the user's own chat with the reg bot (closes the alpha loop).
    Best-effort: a failed note never re-triggers a publish."""
    if not cfg.token:
        return
    try:
        bot = telegram_factory.bot_for(cfg.token)
        bot.send(i18n.text(user.get("lang"), "album_published", n=n), chat_id=user["user_id"])
    except Exception as e:
        log.warning("album note failed for user %s: %s", user["user_id"], redact(e))


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
