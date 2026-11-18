"""Poll-loop tests over duck-typed fakes: the Gmail seam, the MultiBot factory and a real SQLite
State on tmp_path. No HTTP anywhere: the fake bots replace tg_botkit at the factory boundary."""
import base64
import time

import pytest

from tg_garmin_to_telegram.config import Config
from tg_garmin_to_telegram.runner import poll_once, start_text
from tg_garmin_to_telegram.state import State

LINK = "https://livetrack.garmin.com/session/abc-123/token/TOK"
TO = "garmin-telegram+tok1@exiosv.com"


def _msg(link, to=TO, internal_date=None):
    """A Gmail API messages.get(full) shaped mail carrying one LiveTrack link."""
    data = base64.urlsafe_b64encode(f'<a href="{link}">LiveTrack</a>'.encode()).decode()
    msg = {"payload": {"parts": [{"body": {"data": data}}],
                       "headers": [{"name": "To", "value": to}]}}
    if internal_date is not None:
        msg["internalDate"] = str(int(internal_date * 1000))
    return msg


class _Bot:
    """Duck-typed tg_botkit.Bot: records sends; getUpdates returns canned updates or raises."""

    def __init__(self, token):
        self.token = token
        self.sent = []
        self.fail = 0
        self.updates_error = None
        self.canned_updates = []
        self.update_calls = []

    def send(self, text, chat_id=None, silent=False):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("telegram down")
        self.sent.append({"chat_id": chat_id, "text": text})
        return 700 + len(self.sent)

    def updates(self, offset, wait=30, allowed=("message",)):
        self.update_calls.append(offset)
        if self.updates_error:
            raise self.updates_error
        return self.canned_updates


class _Factory:
    """Duck-typed MultiBot: one _Bot per token, remembers every requested token."""

    def __init__(self):
        self._bots = {}
        self.tokens = []

    def bot_for(self, token):
        self.tokens.append(token)
        if token not in self._bots:
            self._bots[token] = _Bot(token)
        return self._bots[token]

    def bot(self, token):
        return self._bots.get(token)  # None when the poll pass never touched this token


class _Gmail:
    def __init__(self, ids, msgs):
        self.ids, self.msgs = ids, msgs

    def list_ids(self, q):
        return self.ids

    def get(self, i):
        return self.msgs[i]


def _user(st, user_id=1, token="tok1", mode="own", bot_token="111:AAA", target=-100123):
    st.touch_user(user_id)
    st.set_target(user_id, mode, "group", target, "Ride group", bot_token)
    st.set_email_link(user_id, token, f"rider{user_id}@example.com")
    st.set_user_state(user_id, "active")


def _initialized(tmp_path) -> State:
    st = State(str(tmp_path))
    poll_once(Config(), _Gmail([], {}), _Factory(), st)  # empty first pass arms the post gate
    return st


def test_new_user_mail_posted_once_with_ride_row(tmp_path):
    st, cfg, f = _initialized(tmp_path), Config(), _Factory()
    _user(st)
    gm = _Gmail(["m1"], {"m1": _msg(LINK)})
    assert poll_once(cfg, gm, f, st) == 1
    assert set(f.tokens) == {"111:AAA"}  # delivery_mode 'own': posted as the user's own bot
    sent = f.bot("111:AAA").sent
    assert len(sent) == 1 and sent[0]["chat_id"] == -100123
    assert "начал активность" in sent[0]["text"] and LINK in sent[0]["text"]
    assert "Смотреть на карту" in sent[0]["text"]
    ride = st.get_ride("abc-123")
    assert ride["user_id"] == 1 and ride["state"] == "started"
    assert ride["channel_msg_id"] == 701 and ride["live_url"] == LINK
    assert ride["started_ts"] is not None and ride["discuss_msg_id"] is None
    assert st.mail_seen("m1")  # marked only after the send succeeded
    poll_once(cfg, gm, f, st)
    assert len(f.bot("111:AAA").sent) == 1  # no duplicate on the next pass


def test_dedupe_same_mail_id_and_same_link_in_two_mails(tmp_path):
    st, cfg, f = _initialized(tmp_path), Config(), _Factory()
    _user(st)
    # Gmail lists newest first; two mails carry the same session link (auto-forward + manual forward)
    gm = _Gmail(["m2", "m1"], {"m1": _msg(LINK), "m2": _msg(LINK)})
    assert poll_once(cfg, gm, f, st) == 1
    poll_once(cfg, gm, f, st)
    assert len(f.bot("111:AAA").sent) == 1  # link dedupe: the second mail is never announced


def test_unknown_plus_token_skipped_silently(tmp_path):
    st, cfg, f = _initialized(tmp_path), Config(), _Factory()
    _user(st)
    gm = _Gmail(["m1"], {"m1": _msg(LINK, to="garmin-telegram+nobody@exiosv.com")})
    assert poll_once(cfg, gm, f, st) == 0
    assert f.bot("111:AAA") is None  # the user's bot was never even constructed
    assert st.mail_seen("m1") and st.get_ride("abc-123") is None


def test_first_run_backlog_is_marked_seen_not_posted(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    _user(st)
    gm = _Gmail(["m1"], {"m1": _msg(LINK)})
    assert poll_once(Config(), gm, f, st) == 0
    assert f.bot("111:AAA") is None
    assert st.mail_seen("m1") and st.get_ride("abc-123") is None


def test_too_old_mail_is_marked_seen_not_posted(tmp_path):
    st, cfg, f = _initialized(tmp_path), Config(max_age_hours=12), _Factory()
    _user(st)
    old = _msg(LINK, internal_date=time.time() - 20 * 3600)
    gm = _Gmail(["m1"], {"m1": old})
    assert poll_once(cfg, gm, f, st) == 0
    assert f.bot("111:AAA") is None
    assert st.mail_seen("m1")


def test_send_failure_not_marked_and_retried_next_poll(tmp_path):
    st, cfg, f = _initialized(tmp_path), Config(), _Factory()
    _user(st)
    f.bot_for("111:AAA").fail = 1  # the next send raises
    gm = _Gmail(["m1"], {"m1": _msg(LINK)})
    with pytest.raises(RuntimeError):
        poll_once(cfg, gm, f, st)  # the loop survives via run_forever; the mail stays unseen
    assert not st.mail_seen("m1") and st.get_ride("abc-123") is None
    poll_once(cfg, gm, f, st)
    assert len(f.bot("111:AAA").sent) == 1  # retried exactly once after recovery
    assert st.mail_seen("m1") and st.get_ride("abc-123")["state"] == "started"
    poll_once(cfg, gm, f, st)
    assert len(f.bot("111:AAA").sent) == 1


def test_send_marked_pending_when_the_db_write_fails(tmp_path):
    st, cfg, f = _initialized(tmp_path), Config(), _Factory()
    _user(st)
    original = st.mark_seen
    calls = {"n": 0}

    def flaky_mark(mail_id, link=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        original(mail_id, link)

    st.mark_seen = flaky_mark  # the post is already public: the pending set must prevent a re-post
    gm = _Gmail(["m1"], {"m1": _msg(LINK)})
    assert poll_once(cfg, gm, f, st) == 1
    poll_once(cfg, gm, f, st)
    assert len(f.bot("111:AAA").sent) == 1
    assert calls["n"] == 2 and st.mail_seen("m1")


def test_delivery_mode_our_posts_with_the_service_bot(tmp_path):
    st, f = _initialized(tmp_path), _Factory()
    _user(st, user_id=2, token="tok2", mode="our", bot_token="222:OWN")
    cfg = Config(token="999:REG")
    gm = _Gmail(["m1"], {"m1": _msg(LINK, to="garmin-telegram+tok2@exiosv.com")})
    assert poll_once(cfg, gm, f, st) == 1
    assert "999:REG" in f.tokens and "222:OWN" not in f.tokens
    assert f.bot("999:REG").sent[0]["chat_id"] == -100123
    assert st.get_ride("abc-123")["user_id"] == 2


def test_own_mode_uses_the_user_bot_token(tmp_path):
    st, cfg, f = _initialized(tmp_path), Config(token="999:REG"), _Factory()
    _user(st, mode="own", bot_token="222:OWN")
    gm = _Gmail(["m1"], {"m1": _msg(LINK)})
    assert poll_once(cfg, gm, f, st) == 1
    assert "222:OWN" in f.tokens and "999:REG" not in f.tokens


def _auto_forward(channel_msg_id, discuss_chat=-100999, update_id=10):
    return {"update_id": update_id, "message": {
        "message_id": 42, "chat": {"id": discuss_chat}, "is_automatic_forward": True,
        "forward_origin": {"chat": {"id": -100123}, "message_id": channel_msg_id},
    }}


def test_thread_discovery_links_the_auto_forward(tmp_path):
    st, cfg, f = _initialized(tmp_path), Config(), _Factory()
    _user(st)
    poll_once(cfg, _Gmail(["m1"], {"m1": _msg(LINK)}), f, st)
    channel_msg_id = st.get_ride("abc-123")["channel_msg_id"]
    f.bot_for("111:AAA").canned_updates = [_auto_forward(channel_msg_id)]
    poll_once(cfg, _Gmail([], {}), f, st)  # empty mailbox: only thread discovery runs
    assert st.get_ride("abc-123")["discuss_msg_id"] == 42
    assert st.get_meta("tg_offset:1") == "11"  # the per-user offset advanced past the update


def test_thread_discovery_broken_get_updates_never_crashes(tmp_path):
    st, cfg, f = _initialized(tmp_path), Config(), _Factory()
    _user(st)
    f.bot_for("111:AAA").updates_error = RuntimeError("getUpdates broken")
    assert poll_once(cfg, _Gmail(["m1"], {"m1": _msg(LINK)}), f, st) == 1
    assert st.get_ride("abc-123")["discuss_msg_id"] is None  # best-effort: stays NULL
    assert st.mail_seen("m1")  # mail polling is unaffected


def test_start_text_is_localised_and_escapes():
    en = start_text({"lang": "en", "garmin_name": "Ivan", "target_title": "g"}, "x", LINK)
    assert "Ivan started an activity" in en and f'href="{LINK}"' in en and "View on map" in en
    ru = start_text({"lang": "ru", "garmin_name": "", "target_title": None}, "Ivan", LINK)
    assert "Ivan начал активность" in ru and "Смотреть на карту" in ru
    tricky = start_text({"lang": "en", "garmin_name": "<b>&x"}, "x", LINK)
    assert "&lt;b&gt;&amp;x" in tricky  # the name is data, never markup
