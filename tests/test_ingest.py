"""F1b ingest API tests: the real server on an ephemeral port, urllib helpers like the to-osm
donor's test_web_api.py. No secrets in the payloads."""
import json
import threading
import urllib.request

import pytest

from tg_garmin_to_telegram import photos as photos_mod
from tg_garmin_to_telegram.ingest import serve
from tg_garmin_to_telegram.state import State


@pytest.fixture()
def api(tmp_path):
    st = State(str(tmp_path))
    httpd = serve(0, "INGESTTESTTOKEN", st)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield st, f"http://127.0.0.1:{port}"
    httpd.shutdown()


@pytest.fixture()
def api_fs(tmp_path):
    """The same server with a photos root wired in: DELETE wipes the buffered files too."""
    st = State(str(tmp_path))
    root = tmp_path / "photos"
    httpd = serve(0, "INGESTTESTTOKEN", st, email_domain="exiosv.com", photos_root=root)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield st, f"http://127.0.0.1:{port}", root
    httpd.shutdown()


def _get(base, path, token=None):
    req = urllib.request.Request(base + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(base, path, token, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _delete(base, path, token=None):
    req = urllib.request.Request(base + path, method="DELETE")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_ping_unauthenticated(api):
    st, base = api
    code, body = _get(base, "/ping")
    assert code == 200 and body["ok"] is True


def test_metrics_unauthenticated(api):
    """Prometheus text exposition: VictoriaMetrics scrapes this — the format must stay text/plain."""
    st, base = api
    st.start_ride(5, "s-metrics", "https://livetrack.garmin.com/session/s-metrics", None, 1)
    req = urllib.request.Request(base + "/metrics")
    with urllib.request.urlopen(req, timeout=10) as r:
        code = r.status
        ctype = r.headers.get("Content-Type", "")
        text = r.read().decode()
    assert code == 200
    assert "version=0.0.4" in ctype
    assert "users_total" in text and 'rides_by_state{state="' in text


def test_user_requires_bearer(api):
    st, base = api
    st.touch_user(5)
    code, body = _get(base, "/api/users/5")
    assert code == 401
    code, body = _get(base, "/api/users/5", token="INGESTTESTTOKEN")
    assert code == 200 and body["user"]["user_id"] == 5


def test_user_row_strips_secrets(api):
    st, base = api
    st.touch_user(5)
    st.set_target(5, "own", "group", -100123, "Ride group", "111:SECRET")
    st.set_garmin_key(5, "GARMINSECRET")
    code, body = _get(base, "/api/users/5", token="INGESTTESTTOKEN")
    u = body["user"]
    assert u["target_id"] == -100123 and u["target_title"] == "Ride group"
    assert "bot_token" not in u and "garmin_api_key" not in u
    assert u.get("garmin_name") == ""  # the key is stripped; the name alone is public


def test_unknown_user_404(api):
    st, base = api
    code, body = _get(base, "/api/users/999", token="INGESTTESTTOKEN")
    assert code == 404


def test_post_target_updates_row(api):
    st, base = api
    st.touch_user(5)
    code, body = _post(base, "/api/users/5/target", "INGESTTESTTOKEN", {
        "delivery_mode": "own", "target_type": "supergroup",
        "target_id": -100123, "target_title": "Ride group", "bot_token": "111:NEWBOT",
    })
    assert code == 200 and body["user"]["state"] == "setup"
    st_row = st.user(5)
    assert st_row["bot_token"] == "111:NEWBOT" and st_row["target_id"] == -100123


def test_post_target_bad_mode_400(api):
    st, base = api
    st.touch_user(5)
    code, body = _post(base, "/api/users/5/target", "INGESTTESTTOKEN", {
        "delivery_mode": "banana", "target_id": -100123,
    })
    assert code == 400


def test_post_bad_json_400(api):
    st, base = api
    req = urllib.request.Request(base + "/api/users/5/target", data=b"{broken", method="POST")
    req.add_header("Authorization", "Bearer INGESTTESTTOKEN")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 400


def test_unauthorized_post_401(api):
    st, base = api
    code, body = _post(base, "/api/users/5/target", None, {"delivery_mode": "own"})
    assert code == 401


# ---- F3: the garmin-telegram-web contract (POST /api/accounts/telegram) and user deletion -----

def test_post_accounts_telegram_creates_user_with_token(api):
    st, base = api
    code, body = _post(base, "/api/accounts/telegram", "INGESTTESTTOKEN",
                       {"uid": 9, "chat_id": 9, "lang": "en"})
    assert code == 200 and body["ok"] is True
    u = body["user"]  # the contract shape: {"ok": true, "user": {...stripped...}}
    assert u["user_id"] == 9 and u["lang"] == "en" and u["state"] == "setup"
    assert u["email_token"] and u["email_addr"].startswith("garmin-telegram+")
    assert u["email_addr"].endswith("@exiosv.com")
    assert "bot_token" not in u and "garmin_api_key" not in u
    assert st.user(9)["email_token"] == u["email_token"]
    # a second call keeps the address and applies the new language
    code, body = _post(base, "/api/accounts/telegram", "INGESTTESTTOKEN", {"uid": 9, "lang": "ru"})
    assert code == 200 and body["user"]["email_token"] == u["email_token"]
    assert body["user"]["lang"] == "ru"


def test_post_accounts_telegram_bad_uid_400(api):
    st, base = api
    for payload in ({}, {"uid": None}, {"uid": "abc"}, {"uid": 0}, {"uid": -5}):
        code, body = _post(base, "/api/accounts/telegram", "INGESTTESTTOKEN", payload)
        assert code == 400 and body["ok"] is False
    assert st.db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_post_accounts_telegram_requires_bearer(api):
    st, base = api
    code, body = _post(base, "/api/accounts/telegram", None, {"uid": 9})
    assert code == 401 and st.user(9) is None


def test_delete_user_removes_rows(api):
    st, base = api
    st.touch_user(7)
    st.set_email_link(7, "tok7", "rider@example.com")
    st.set_user_state(7, "active")
    st.set_target(7, "own", "group", -100123, "G", "111:AAA")
    st.start_ride(7, "s1", "https://livetrack.garmin.com/session/s1", 500, 100)
    st.mark_seen("m1", "https://livetrack.garmin.com/session/s1", user_id=7)
    st.mark_seen("global-mail")  # a mail with no matched user: must survive
    st.set_meta("pending:7", "x")
    st.set_meta("tg_offset:7", "5")
    st.set_meta("gride_check:7", "1")
    st.set_meta("no_ride_note:7", "2")
    st.set_meta("init", "1")  # a global key: must survive
    st.add_event(7, "announced", {})
    ride_id = st.get_ride("s1")["ride_id"]
    st.add_photo(ride_id, "1.jpg")
    code, body = _delete(base, "/api/users/7", "INGESTTESTTOKEN")
    assert code == 200 and body == {"ok": True}
    assert st.user(7) is None
    q = lambda sql: st.db.execute(sql).fetchone()[0]
    assert q("SELECT COUNT(*) FROM rides WHERE user_id=7") == 0
    assert q("SELECT COUNT(*) FROM seen WHERE user_id=7") == 0
    assert st.mail_seen("global-mail")  # the anonymous dedupe row is untouched
    assert st.get_meta("pending:7") is None and st.get_meta("tg_offset:7") is None
    assert st.get_meta("gride_check:7") is None and st.get_meta("no_ride_note:7") is None
    assert st.get_meta("init") == "1"
    assert q("SELECT COUNT(*) FROM events WHERE user_id=7") == 0
    assert q("SELECT COUNT(*) FROM photo_buf") == 0
    code, _ = _delete(base, "/api/users/7", "INGESTTESTTOKEN")
    assert code == 404  # the second delete has nothing to remove


def test_delete_user_wipes_photo_files(api_fs):
    st, base, root = api_fs
    st.touch_user(7)
    st.start_ride(7, "s1", "u", None, 100)
    ride_id = st.get_ride("s1")["ride_id"]
    photos_mod.save(root, ride_id, 1, b"x")
    assert (root / str(ride_id) / "1.jpg").is_file()
    code, _ = _delete(base, "/api/users/7", "INGESTTESTTOKEN")
    assert code == 200
    assert not (root / str(ride_id)).exists()  # the buffered files went with the rows


def test_delete_requires_bearer(api):
    st, base = api
    st.touch_user(7)
    code, body = _delete(base, "/api/users/7", None)
    assert code == 401 and st.user(7) is not None


def test_delete_unknown_user_404(api):
    st, base = api
    code, body = _delete(base, "/api/users/999", "INGESTTESTTOKEN")
    assert code == 404
