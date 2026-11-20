"""F1b onboarding tests: duck-typed bots, real SQLite on tmp_path. The service bot's getUpdates
is canned; the Garmin validation is faked at the client boundary."""
import json
import time
from datetime import datetime, timedelta

import pytest

from tg_garmin_to_telegram import onboarding
from tg_garmin_to_telegram.config import Config
from tg_garmin_to_telegram.state import State


class _Bot:
    def __init__(self, token):
        self.token = token
        self.sent = []
        self.deleted = []
        self.updates_error = None
        self.canned_updates = []
        self.whoami_error = None
        self.update_calls = []

    def call(self, method, **data):
        if method == "sendMessage":
            self.sent.append({"chat_id": data.get("chat_id"), "text": data.get("text")})
            return {"message_id": 800 + len(self.sent)}
        return {}

    def send(self, text, chat_id=None, **kw):
        self.sent.append({"chat_id": chat_id, "text": text})
        return 800 + len(self.sent)

    def delete(self, message_id, chat_id=None):
        self.deleted.append((chat_id, message_id))
        return True

    def whoami(self):
        if self.whoami_error:
            raise self.whoami_error
        return {"id": 42, "username": "their_bot"}

    def updates(self, offset, wait=30, allowed=("message",)):
        self.update_calls.append(offset)
        if self.updates_error:
            raise self.updates_error
        return self.canned_updates


class _Factory:
    def __init__(self):
        self._bots = {}
        self.tokens = []

    def bot_for(self, token):
        self.tokens.append(token)
        if token not in self._bots:
            self._bots[token] = _Bot(token)
        return self._bots[token]


def _msg(uid=5, text=None, lang="ru"):
    msg = {"message_id": 50, "from": {"id": uid, "language_code": lang,
                                      "first_name": "Igor"}, "chat": {"id": uid}}
    if text is not None:
        msg["text"] = text
    return msg


def _cfg(**kw):
    return Config(token="999:REG", email_domain="exiosv.com", **kw)


def _update(update_id, **body):
    return {"update_id": update_id, **body}


def test_start_creates_user_with_email_token(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    u = st.user(5)
    assert u and u["email_token"] and u["email_addr"].startswith("garmin-telegram+")
    assert u["email_addr"].endswith("@exiosv.com") and u["state"] == "setup"
    sent = f.bot_for("999:REG").sent
    assert any("Garmin" in m["text"] for m in sent)  # the /start flow text went out


def test_own_bot_token_deleted_and_validated(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:own_bot"})
    f.bot_for("999:REG").sent.clear()
    f.bot_for("999:REG").deleted.clear()
    onboarding.handle_message(cfg, st, f, _msg(uid=5, text="111:USERBOT"))
    assert f.bot_for("999:REG").deleted  # the token message is gone immediately
    assert st.user(5)["bot_token"] == "111:USERBOT"
    assert "their_bot" in f.bot_for("999:REG").sent[-1]["text"]


def test_own_bot_invalid_token_rejected(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:own_bot"})
    f._bots["111:BROKEN"] = _Bot("111:BROKEN")
    f._bots["111:BROKEN"].whoami_error = RuntimeError("401 unauthorized")
    onboarding.handle_message(cfg, st, f, _msg(uid=5, text="111:BROKEN"))
    assert not st.user(5)["bot_token"]  # nothing saved
    assert "не работает" in f.bot_for("999:REG").sent[-1]["text"]


def test_my_chat_member_confirm_sets_target(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    st.set_own_bot_token(5, "111:USERBOT")  # the token step is done before the chat confirm
    update = {"my_chat_member": {
        "chat": {"id": -100777, "title": "Ride chat", "type": "supergroup"},
        "from": {"id": 5},
        "new_chat_member": {"status": "administrator"},
    }}
    onboarding.handle_own_chat_member(cfg, st, f, update)
    # the confirm button press finalizes the target
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:chat_yes"})
    u = st.user(5)
    assert u["state"] == "active" and u["delivery_mode"] == "own"
    assert u["target_id"] == -100777 and u["target_title"] == "Ride chat"


def test_service_bot_added_sets_our_mode(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    update = {"my_chat_member": {
        "chat": {"id": -100888, "title": "My rides", "type": "supergroup"},
        "from": {"id": 5},
        "new_chat_member": {"status": "administrator", "user": {"username": "garmin_start_bot"}},
    }}
    onboarding.handle_service_chat_member(cfg, st, f, update)
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:chat_yes"})
    u = st.user(5)
    assert u["state"] == "active" and u["delivery_mode"] == "our"
    assert u["target_id"] == -100888


def test_lang_command_switches_language(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    onboarding.handle_message(cfg, st, f, _msg(uid=5, text="/lang en"))
    assert st.user(5)["lang"] == "en"
    onboarding.handle_message(cfg, st, f, _msg(uid=5, text="/status"))
    texts = [m["text"] for m in f.bot_for("999:REG").sent]
    assert any("Status" in t for t in texts)


def test_status_renders_address(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:status"})
    sent = f.bot_for("999:REG").sent[-1]["text"]
    assert "garmin-telegram+" in sent and "настройка" in sent


def test_garmin_key_flow_validates(tmp_path, monkeypatch):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:garmin_key"})

    class _FakeClient:
        def __init__(self, key):
            self.key = key

        def recent_activities(self, limit=10):
            if self.key != "GOOD_KEY":
                raise onboarding.GarminApiError("exchange failed", 401)
            return [{"owner_name": "Vespex", "activityName": "Ride"}]

    monkeypatch.setattr(onboarding, "GarminApiClient", _FakeClient)
    onboarding.handle_message(cfg, st, f, _msg(uid=5, text="BAD_KEY"))
    assert "не работает" in f.bot_for("999:REG").sent[-1]["text"] and "auth" in f.bot_for("999:REG").sent[-1]["text"]
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:garmin_key"})  # re-enter the dialog
    onboarding.handle_message(cfg, st, f, _msg(uid=5, text="GOOD_KEY"))
    u = st.user(5)
    assert u["garmin_api_key"] == "GOOD_KEY" and u["garmin_name"] == "Vespex"


def test_offset_advances_and_bad_update_does_not_crash(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    f.bot_for("999:REG").canned_updates = [
        _update(10, message={"from": {"id": 5}}),  # no text: unhandled but offset moves
        _update(11, weird="payload"),
    ]
    onboarding.poll_once(cfg, st, f)
    assert st.get_meta("onb_offset") == "12"
    assert st.get_meta(ONB_OFFSET_alias := "onb_offset") == "12"


def test_get_updates_failure_never_raises(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    f.bot_for("999:REG").updates_error = RuntimeError("telegram down")
    onboarding.poll_once(cfg, st, f)  # must not raise
    assert st.get_meta("onb_offset", "0") == "0"


def test_setup_own_user_chat_member_reaches_confirm(tmp_path):
    """The dead-end fix: a setup user who linked their bot gets their bot's my_chat_member watched
    (poll_discussions covers setup 'own' users), the confirm goes out, and the chat_yes finalizes."""
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:own_bot"})
    onboarding.handle_message(cfg, st, f, _msg(uid=5, text="111:USERBOT"))
    # the user is still 'setup' with a target_id NULL: the old code never polled them
    assert st.user(5)["state"] == "setup" and st.user(5)["target_id"] is None
    f.bot_for("111:USERBOT").canned_updates = [{
        "update_id": 5,
        "my_chat_member": {"chat": {"id": -100777, "title": "Ride chat", "type": "supergroup"},
                           "from": {"id": 5},
                           "new_chat_member": {"status": "administrator"}},
    }]
    from tg_garmin_to_telegram import runner as runner_mod
    onboarding.handle_own_chat_member  # exists
    runner_mod.set_chat_member_handler(lambda state, update:
                                       onboarding.handle_own_chat_member(cfg, state, f, update))
    runner_mod.poll_discussions(f, st)
    confirm = f.bot_for("999:REG").sent[-1]["text"]
    assert "Ride chat" in confirm and "группа" in confirm  # a real kind word, not an emoji
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:chat_yes"})
    u = st.user(5)
    assert u["state"] == "active" and u["target_id"] == -100777


def test_cancel_and_bare_lang_wording(tmp_path):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg()
    onboarding.handle_message(cfg, st, f, _msg(text="/start"))
    onboarding.handle_callback(cfg, st, f, {"from": {"id": 5}, "data": "onb:own_bot"})
    onboarding.handle_message(cfg, st, f, _msg(uid=5, text="/cancel"))
    assert "отменён" in f.bot_for("999:REG").sent[-1]["text"]
    onboarding.handle_message(cfg, st, f, _msg(uid=5, text="/lang"))
    assert "Язык сообщений: RU" in f.bot_for("999:REG").sent[-1]["text"]  # shown, not switched


def test_redact_covers_bare_token(tmp_path):
    from tg_garmin_to_telegram.redact import redact
    out = redact("sendMessage: chat not found for 1234567:AAE_ab-XYZ_very_long_token_part")
    assert "1234567" not in out and "AAE_ab" not in out


# ---- F3: the photo buffer (alpha) ---------------------------------------------------------------

def _photo_msg(uid=5, mid=60, sizes=None):
    """A Telegram photo message: several sizes, ascending (the largest carries the bytes)."""
    return {"message_id": mid, "from": {"id": uid, "language_code": "ru"}, "chat": {"id": uid},
            "photo": sizes or [
                {"file_id": "fid_small", "width": 100, "height": 100, "file_size": 1000},
                {"file_id": "fid_big", "width": 1200, "height": 900, "file_size": 50000},
            ]}


def _started_ride(st, uid=5, ts=1000):
    st.start_ride(uid, "sess-1", "https://livetrack.garmin.com/session/s1", None, ts)


def test_photo_buffers_file_and_replies(tmp_path, monkeypatch):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg(photo_dir=str(tmp_path / "photos"))
    _started_ride(st)
    seen = []

    def fake_download(c, bot, file_id):
        seen.append(file_id)
        return b"JPEGDATA"

    monkeypatch.setattr(onboarding, "_download_photo", fake_download)
    onboarding.handle_photo(cfg, st, f, _photo_msg())
    assert seen == ["fid_big"]  # the largest size is the one downloaded
    ride = st.get_ride("sess-1")
    assert st.count_photos(ride["ride_id"]) == 1
    path = st.ride_photos(ride["ride_id"])[0]["path"]
    with open(path, "rb") as fh:
        assert fh.read() == b"JPEGDATA"
    texts = [m["text"] for m in f.bot_for("999:REG").sent]
    assert any("сохранено" in t for t in texts)


def test_photo_download_uses_the_bot_api_file_endpoint(tmp_path, monkeypatch):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg(api_base="http://127.0.0.1:8081")
    captured = {}

    class _R:
        status_code = 200
        content = b"BYTES"

    def fake_get(url, timeout=60):
        captured["url"] = url
        return _R()

    monkeypatch.setattr(onboarding.requests, "get", fake_get)
    bot = f.bot_for("999:REG")
    monkeypatch.setattr(bot, "call",
                        lambda method, **data: {"file_path": "photos/file_3.jpg"})
    assert onboarding._download_photo(cfg, bot, "fid") == b"BYTES"
    assert captured["url"].startswith("http://127.0.0.1:8081/file/bot999:REG/photos/file_3.jpg")
    monkeypatch.setattr(_R, "status_code", 404)
    assert onboarding._download_photo(cfg, bot, "fid") is None  # a bad status never raises


def test_no_active_ride_hint_once_per_window(tmp_path, monkeypatch):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg(photo_dir=str(tmp_path / "photos"))
    onboarding.handle_photo(cfg, st, f, _photo_msg())
    texts = [m["text"] for m in f.bot_for("999:REG").sent]
    assert any("Активной поездки" in t for t in texts)
    f.bot_for("999:REG").sent.clear()
    onboarding.handle_photo(cfg, st, f, _photo_msg(mid=61))
    assert f.bot_for("999:REG").sent == []  # a photo burst does not spam the hint
    st.set_meta("no_ride_note:5", str(int(time.time()) - 601))  # the window has passed
    onboarding.handle_photo(cfg, st, f, _photo_msg(mid=62))
    assert len(f.bot_for("999:REG").sent) == 1


def test_photo_too_big_is_refused(tmp_path, monkeypatch):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg(photo_dir=str(tmp_path / "photos"), photo_max_bytes=1000)
    _started_ride(st)
    called = []
    monkeypatch.setattr(onboarding, "_download_photo",
                        lambda c, b, fid: called.append(fid) or b"x")
    onboarding.handle_photo(cfg, st, f, _photo_msg())  # file_size 50000 > 1000
    assert called == []  # refused before any download
    ride = st.get_ride("sess-1")
    assert st.count_photos(ride["ride_id"]) == 0
    texts = [m["text"] for m in f.bot_for("999:REG").sent]
    assert any("больше" in t for t in texts)


def test_photo_limit_caps_per_ride(tmp_path, monkeypatch):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg(photo_dir=str(tmp_path / "photos"), photo_max_per_ride=2)
    _started_ride(st)
    ride_id = st.get_ride("sess-1")["ride_id"]
    st.add_photo(ride_id, "1.jpg")
    st.add_photo(ride_id, "2.jpg")
    monkeypatch.setattr(onboarding, "_download_photo", lambda c, b, fid: b"JPEGDATA")
    onboarding.handle_photo(cfg, st, f, _photo_msg())
    assert st.count_photos(ride_id) == 2  # the third photo is not buffered
    texts = [m["text"] for m in f.bot_for("999:REG").sent]
    assert any("Лимит" in t for t in texts)


def test_poll_once_routes_photo_messages(tmp_path, monkeypatch):
    st, f = State(str(tmp_path)), _Factory()
    cfg = _cfg(photo_dir=str(tmp_path / "photos"))
    _started_ride(st)
    monkeypatch.setattr(onboarding, "_download_photo", lambda c, b, fid: b"JPEGDATA")
    f.bot_for("999:REG").canned_updates = [_update(30, message=_photo_msg())]
    onboarding.poll_once(cfg, st, f)
    assert st.count_photos(st.get_ride("sess-1")["ride_id"]) == 1
    assert st.get_meta("onb_offset") == "31"  # the offset advanced past the photo update
