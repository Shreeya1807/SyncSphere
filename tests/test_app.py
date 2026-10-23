import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from syncsphere import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-only",
            "DATABASE_PATH": tmp_path / "test.sqlite",
            "MONGO_URL": None,
            "REDIS_URL": None,
        }
    )


def account(app, name):
    client = app.test_client()
    csrf = client.get("/api/session").json["csrf"]
    result = client.post(
        "/api/register",
        json={"username": name, "password": "long-test-password"},
        headers={"X-CSRF-Token": csrf},
    )
    assert result.status_code == 200
    return client, {"X-CSRF-Token": result.json["csrf"]}


def test_auth_csrf_and_password_hashing(app):
    client, headers = account(app, "alice")
    assert client.post("/api/rooms", json={"name": "No token"}).status_code == 403
    saved = app.extensions["store"].get("user:alice")
    assert "long-test-password" not in str(saved)
    assert client.post("/api/logout", json={}, headers=headers).status_code == 200
    assert client.get("/api/rooms").status_code == 401


def test_message_authorization_ack_deduplication_and_recovery(app):
    alice, ah = account(app, "alice")
    bob, bh = account(app, "bobby")
    room = alice.post("/api/rooms", json={"name": "Design"}, headers=ah).json
    assert bob.get("/api/rooms/" + room["id"]).status_code == 404
    socket = app.extensions["socketio"]
    a = socket.test_client(
        app, flask_test_client=alice, auth={"csrf": ah["X-CSRF-Token"]}
    )
    b = socket.test_client(
        app, flask_test_client=bob, auth={"csrf": bh["X-CSRF-Token"]}
    )
    assert b.emit("join", {"room": room["id"]}, callback=True)["ok"] is False
    bob.post("/api/join", json={"invite": room["invite"]}, headers=bh)
    assert b.emit("join", {"room": room["id"]}, callback=True)["ok"]
    payload = {"room": room["id"], "client_id": "a" * 20, "text": "Hello team"}
    first = a.emit("message", payload, callback=True)
    assert first["ok"]
    second = a.emit("message", payload, callback=True)
    assert first == second
    assert len(alice.get("/api/rooms/" + room["id"]).json["messages"]) == 1
    assert any(e["name"] == "message" for e in b.get_received())
    b.disconnect()
    a.emit(
        "message",
        {**payload, "client_id": "b" * 20, "text": "While offline"},
        callback=True,
    )
    recovered = bob.get("/api/rooms/" + room["id"] + "?after=1").json
    assert [m["text"] for m in recovered["messages"]] == ["While offline"]
    assert (
        a.emit("message", {**payload, "text": "Conflicting retry"}, callback=True)["ok"]
        is False
    )


def test_files_are_membership_scoped(app):
    alice, ah = account(app, "alice")
    bob, bh = account(app, "bobby")
    room = alice.post("/api/rooms", json={"name": "Files"}, headers=ah).json
    result = alice.post(
        f"/api/rooms/{room['id']}/files",
        data={"file": (io.BytesIO(b"notes"), "notes.txt")},
        headers=ah,
    )
    assert result.status_code == 201
    url = "/api/files/" + result.json["id"]
    assert bob.get(url).status_code == 404
    assert alice.get(url).data == b"notes"
    assert "attachment" in alice.get(url).headers["Content-Disposition"]
    rejected = alice.post(
        f"/api/rooms/{room['id']}/files",
        data={"file": (io.BytesIO(b"code"), "evil.html")},
        headers=ah,
    )
    assert rejected.status_code == 400


def test_session_revocation_blocks_existing_socket(app):
    alice, ah = account(app, "alice")
    room = alice.post("/api/rooms", json={"name": "Private"}, headers=ah).json
    socket = app.extensions["socketio"].test_client(
        app, flask_test_client=alice, auth={"csrf": ah["X-CSRF-Token"]}
    )
    alice.post("/api/logout", json={}, headers=ah)
    assert (
        socket.emit(
            "message",
            {"room": room["id"], "client_id": "x" * 20, "text": "Unauthorized"},
            callback=True,
        )["ok"]
        is False
    )


def test_atomic_updates_and_restart_persistence(app):
    store = app.extensions["store"]

    def increment(_):
        store.mutate("counter", lambda old: {"n": (old or {"n": 0})["n"] + 1})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(increment, range(50)))
    assert store.get("counter")["n"] == 50
    restarted = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-only",
            "DATABASE_PATH": app.config["DATABASE_PATH"],
            "MONGO_URL": None,
            "REDIS_URL": None,
        }
    )
    assert restarted.extensions["store"].get("counter")["n"] == 50
