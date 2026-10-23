"""Integration contract against real MongoDB, Redis and two server processes."""

import os
import subprocess
import sys
import threading
import time
import uuid

import pytest
import requests
import socketio

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_SERVICES") != "1",
    reason="Requires real MongoDB and Redis",
)


def test_cross_process_delivery_and_restart_recovery():
    env = {
        **os.environ,
        "SECRET_KEY": "integration-only-shared-secret",
        "MONGO_URL": os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
        "REDIS_URL": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    }
    processes = []
    sockets = []
    try:
        for port in [5105, 5106]:
            processes.append(
                subprocess.Popen(
                    [sys.executable, "run.py"],
                    env={**env, "PORT": str(port)},
                    stdout=subprocess.DEVNULL,
                )
            )
            for attempt in range(60):
                try:
                    if requests.get(
                        f"http://localhost:{port}/health/ready", timeout=1
                    ).ok:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.25)
            else:
                raise AssertionError("Server did not become ready")

        def register(port):
            http = requests.Session()
            base = f"http://localhost:{port}"
            csrf = http.get(base + "/api/session", timeout=5).json()["csrf"]
            response = http.post(
                base + "/api/register",
                json={
                    "username": "u" + uuid.uuid4().hex[:12],
                    "password": "integration-password",
                },
                headers={"X-CSRF-Token": csrf},
                timeout=5,
            )
            response.raise_for_status()
            csrf = response.json()["csrf"]
            client = socketio.Client(http_session=http)
            client.connect(base, auth={"csrf": csrf}, transports=["websocket"])
            sockets.append(client)
            return http, client, base, {"X-CSRF-Token": csrf}

        a, sa, basea, ha = register(5105)
        b, sb, baseb, hb = register(5106)
        room = a.post(
            basea + "/api/rooms",
            json={"name": "Multiworker contract"},
            headers=ha,
            timeout=5,
        ).json()
        b.post(
            baseb + "/api/join", json={"invite": room["invite"]}, headers=hb, timeout=5
        ).raise_for_status()
        assert sb.call("join", {"room": room["id"]}, timeout=5)["ok"]
        received = threading.Event()
        sb.on("message", lambda data: received.set())
        payload = {
            "room": room["id"],
            "client_id": uuid.uuid4().hex,
            "text": "Across server processes",
        }
        ack = sa.call("message", payload, timeout=5)
        assert ack["ok"] and received.wait(5)
        assert (
            sa.call("message", payload, timeout=5)["message"]["id"]
            == ack["message"]["id"]
        )
        sb.disconnect()
        processes[1].terminate()
        processes[1].wait(timeout=10)
        payload["client_id"] = uuid.uuid4().hex
        payload["text"] = "Saved while receiver offline"
        assert sa.call("message", payload, timeout=5)["ok"]
        processes[1] = subprocess.Popen(
            [sys.executable, "run.py"],
            env={**env, "PORT": "5106"},
            stdout=subprocess.DEVNULL,
        )
        for attempt in range(60):
            try:
                recovered = b.get(
                    baseb + "/api/rooms/" + room["id"] + "?after=1", timeout=1
                )
                if recovered.ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.25)
        else:
            raise AssertionError("Restart failed")
        assert recovered.json()["messages"][0]["text"] == "Saved while receiver offline"
    finally:
        for client in sockets:
            if client.connected:
                client.disconnect()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
