"""F1b ingest API tests: the real server on an ephemeral port, urllib helpers like the to-osm
donor's test_web_api.py. No secrets in the payloads."""
import json
import threading
import urllib.request

import pytest

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


def test_ping_unauthenticated(api):
    st, base = api
    code, body = _get(base, "/ping")
    assert code == 200 and body["ok"] is True


def test_metrics_unauthenticated(api):
    st, base = api
    code, body = _get(base, "/metrics")
    assert code == 200 and "users_total" in body and "rides_by_state" in body


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
