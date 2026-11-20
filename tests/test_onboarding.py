"""F1b onboarding tests: duck-typed bots, real SQLite on tmp_path. The service bot's getUpdates
is canned; the Garmin validation is faked at the client boundary."""
import json
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
