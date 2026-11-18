import sqlite3

import pytest

from tg_garmin_to_telegram.state import State


def test_users_defaults_and_roundtrip(tmp_path):
    st = State(str(tmp_path))
    u = st.touch_user(42, chat_id=424242)
    assert u["user_id"] == 42 and u["chat_id"] == 424242
    assert u["lang"] == "ru" and u["state"] == "setup" and u["photos_alpha"] == 1
    assert u["delivery_mode"] == "own"
    assert st.user(42) == u
    assert st.user(43) is None


def test_touch_user_is_idempotent(tmp_path):
    st = State(str(tmp_path))
    st.touch_user(1, chat_id=100)
    u = st.touch_user(1, chat_id=200)  # second contact keeps the first values
    assert u["chat_id"] == 100 and u["created_ts"] is not None


def test_email_token_is_unique(tmp_path):
    st = State(str(tmp_path))
    st.touch_user(1)
    st.touch_user(2)
    st.set_email_link(1, "tok-1", "a@example.com")
    st.set_email_link(2, "tok-2", "b@example.com")
    with pytest.raises(sqlite3.IntegrityError):
        st.set_email_link(2, "tok-1", "b@example.com")  # a token collision is a bug and must raise


def test_lookup_by_email_token_and_address(tmp_path):
    st = State(str(tmp_path))
    st.touch_user(7)
    st.set_email_link(7, "tok-7", "rider@example.com")
    assert st.user_by_email_token("tok-7") is None  # setup users are invisible to the poll loop
    st.set_user_state(7, "active")
    assert st.user_by_email_token("tok-7")["user_id"] == 7
    assert st.user_by_email_token("nope") is None
    assert st.user_by_email("rider@example.com")["user_id"] == 7
    assert st.user_by_email("other@example.com") is None


def test_target_and_state(tmp_path):
    st = State(str(tmp_path))
    st.touch_user(5)
    st.set_target(5, "own", "group", -100123, "Ride group", "111:AAA")
    st.set_user_state(5, "active")
    u = st.user(5)
    assert (u["delivery_mode"], u["target_type"], u["target_id"], u["target_title"]) == \
        ("own", "group", -100123, "Ride group")
    assert u["bot_token"] == "111:AAA" and u["state"] == "active"
    assert st.active_users()[0]["user_id"] == 5
    st.set_user_state(5, "setup")
    assert st.active_users() == []


def test_start_ride_is_idempotent_per_session(tmp_path):
    st = State(str(tmp_path))
    assert st.start_ride(1, "sess-1", "https://livetrack.garmin.com/session/s1/token/T", 555, 100) is True
    assert st.start_ride(1, "sess-1", "https://livetrack.garmin.com/session/s1/token/OTHER", 556, 200) is False
    ride = st.get_ride("sess-1")
    assert ride["live_url"].endswith("/token/T") and ride["started_ts"] == 100 and ride["state"] == "started"
    assert ride["channel_msg_id"] == 555  # the announcement post is stored with the ride, first row wins
    assert st.get_ride("missing") is None


def test_seen_dedupe_roundtrip(tmp_path):
    st = State(str(tmp_path))
    assert not st.initialized() and not st.mail_seen("m1") and not st.link_seen(None)
    st.mark_seen("m1", "https://livetrack.garmin.com/session/s1", user_id=7)
    st.mark_seen("m1", "https://livetrack.garmin.com/session/other", user_id=7)  # INSERT OR IGNORE keeps the first
    st.mark_seen("m2")  # a mail with no link (no matching user, backlog) is still deduped by id
    assert st.mail_seen("m1") and st.mail_seen("m2") and not st.mail_seen("m3")
    assert st.link_seen("https://livetrack.garmin.com/session/s1", 7)
    assert not st.link_seen("https://livetrack.garmin.com/session/s1", 8)  # per-user scope
    assert not st.link_seen("https://livetrack.garmin.com/session/other", 7)
    st.set_initialized()
    assert st.initialized()


def test_discussion_lookup_helpers(tmp_path):
    st = State(str(tmp_path))
    st.start_ride(1, "s1", "u", 501, 100)   # announced, no thread yet: listed
    st.start_ride(1, "s2", "u", None, 100)  # not announced yet: excluded
    st.start_ride(1, "s3", "u", 502, 100)   # announced and already linked: excluded
    st.set_discuss_msg(1, "s3", 900)
    st.start_ride(2, "s4", "u", 501, 100)   # another chat may hold the same message id
    assert st.ride_by_channel_msg(1, 501)["session_id"] == "s1"
    assert st.ride_by_channel_msg(1, 502)["session_id"] == "s3"
    assert st.ride_by_channel_msg(2, 501)["session_id"] == "s4"  # scoped per user
    assert st.ride_by_channel_msg(1, 404) is None
    assert [r["session_id"] for r in st.unlinked_discussion_rides(1)] == ["s1"]
    assert [r["session_id"] for r in st.unlinked_discussion_rides(2)] == ["s4"]
    st.set_discuss_msg(1, "s1", 901)
    assert st.unlinked_discussion_rides(1) == []


def test_ride_lifecycle(tmp_path):
    st = State(str(tmp_path))
    st.start_ride(1, "s1", "https://livetrack.garmin.com/session/s1", started_ts=100)
    assert st.set_channel_msg(1, "s1", 555) is True
    assert st.set_channel_msg(1, "s1", 556) is False  # already attached, never overwritten
    assert st.set_discuss_msg(1, "s1", 777) is True
    st.set_activity(1, "s1", "garmin-987")
    assert st.set_ride_end("s1", 200) is True
    assert st.set_ride_end("s1", 300) is False  # only a 'started' ride transitions
    ride = st.get_ride("s1")
    assert (ride["channel_msg_id"], ride["discuss_msg_id"], ride["ended_ts"]) == (555, 777, 200)
    assert ride["activity_id"] == "garmin-987" and ride["state"] == "ended"


def test_user_rides_filter_and_order(tmp_path):
    st = State(str(tmp_path))
    st.start_ride(1, "s1", "u", started_ts=100)
    st.start_ride(1, "s2", "u", started_ts=300)
    st.start_ride(2, "s3", "u", started_ts=200)
    st.set_ride_end("s1", 150)
    assert [r["session_id"] for r in st.user_rides(1)] == ["s2", "s1"]
    assert [r["session_id"] for r in st.user_rides(1, state="started")] == ["s2"]
    assert [r["session_id"] for r in st.user_rides(2)] == ["s3"]


def test_meta_and_events(tmp_path):
    st = State(str(tmp_path))
    st.set_meta("k", "v1")
    st.set_meta("k", "v2")  # upsert
    assert st.get_meta("k") == "v2"
    assert st.get_meta("missing", "dflt") == "dflt"
    st.delete_meta("k")
    assert st.get_meta("k") is None
    st.touch_user(1)
    st.add_event(1, "announced", {"session_id": "s1"})
    evs = st.events(1)
    assert len(evs) == 1 and evs[0]["kind"] == "announced" and evs[0]["data"] == {"session_id": "s1"}


def test_events_are_trimmed_per_user(tmp_path):
    st = State(str(tmp_path))
    st.touch_user(1)
    for i in range(210):
        st.add_event(1, "tick", {"i": i})
    st.add_event(2, "other", {})  # other users are not affected
    assert len(st.events(1, limit=500)) == 200
    assert st.events(1, limit=500)[0]["data"]["i"] == 209  # newest kept


def test_migrate_adds_missing_columns_to_legacy_db(tmp_path):
    legacy = sqlite3.connect(tmp_path / "state.db")
    legacy.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, chat_id INTEGER, created_ts INTEGER)")
    legacy.execute(
        "CREATE TABLE rides (ride_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
        "session_id TEXT UNIQUE)"
    )
    legacy.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    legacy.execute("INSERT INTO users (user_id, chat_id, created_ts) VALUES (1, -100, 123)")
    legacy.commit()
    legacy.close()
    st = State(str(tmp_path))  # bootstrap must not fail on the old shape
    u = st.user(1)
    assert u["chat_id"] == -100 and u["lang"] == "ru" and u["state"] == "setup"
    assert st.start_ride(1, "s1", "https://livetrack.garmin.com/session/s1", started_ts=10)
    assert st.get_ride("s1")["state"] == "started"
    st.set_email_link(1, "tok", "x@example.com")  # migrated columns are usable
    st.set_user_state(1, "active")
    assert st.user_by_email_token("tok")["user_id"] == 1


def test_upgrade_from_base_commit_schema(tmp_path):
    """The 5fb5b91 schema (seen without user_id, rides with a table-level UNIQUE(session_id))
    must migrate cleanly: the column is added, the constraint is rebuilt per user."""
    legacy = sqlite3.connect(tmp_path / "state.db")
    legacy.execute("CREATE TABLE seen (mail_id TEXT PRIMARY KEY, link TEXT, ts INTEGER)")
    legacy.execute(
        "CREATE TABLE rides (ride_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
        "session_id TEXT UNIQUE, live_url TEXT, started_ts INTEGER, ended_ts INTEGER, "
        "state TEXT DEFAULT 'started', channel_msg_id INTEGER, discuss_msg_id INTEGER, activity_id TEXT)"
    )
    legacy.commit()
    legacy.close()
    st = State(str(tmp_path))
    st.mark_seen("m1", "https://livetrack.garmin.com/session/s1", user_id=7)  # no OperationalError
    assert st.link_seen("https://livetrack.garmin.com/session/s1", 7)
    st.touch_user(7)
    st.touch_user(8)
    assert st.start_ride(7, "s1", "https://livetrack.garmin.com/session/s1", started_ts=10)
    assert st.start_ride(8, "s1", "https://livetrack.garmin.com/session/s1", started_ts=10)  # rebuilt: no legacy UNIQUE


def test_migration_is_idempotent(tmp_path):
    State(str(tmp_path))
    st = State(str(tmp_path))  # reopen: no duplicate-index or column errors
    st.touch_user(9)
    assert st.user(9)["state"] == "setup"
