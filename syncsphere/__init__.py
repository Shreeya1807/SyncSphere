import base64
import hashlib
import os
import re
import secrets
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, session
from flask_socketio import SocketIO, join_room
from werkzeug.security import check_password_hash, generate_password_hash

from .store import Store


def create_app(config=None):
    app = Flask(__name__, static_folder="web", static_url_path="/assets")
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        path = Path("data/session-key")
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.write_text(secrets.token_hex(32))
        secret = path.read_text().strip()
    app.config.update(
        SECRET_KEY=secret,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024 + 4096,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        DATABASE_PATH=os.environ.get("DATABASE_PATH", "data/syncsphere.sqlite"),
        MONGO_URL=os.environ.get("MONGO_URL"),
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        REDIS_URL=os.environ.get("REDIS_URL"),
    )
    if config:
        app.config.update(config)
    store = Store(
        app.config["DATABASE_PATH"],
        app.config.get("MONGO_URL"),
        database_url=app.config.get("DATABASE_URL"),
    )
    socket = SocketIO(
        app,
        async_mode="threading",
        message_queue=app.config.get("REDIS_URL"),
        max_http_buffer_size=16384,
        logger=False,
        engineio_logger=False,
    )
    app.extensions["store"] = store

    def identity():
        token = session.get("token", "")
        record = (
            store.get("session:" + hashlib.sha256(token.encode()).hexdigest())
            if token
            else None
        )
        if not record or record["expires"] < time.time():
            abort(401, "Please sign in")
        return record["username"]

    def limited(key, maximum=60):
        now = int(time.time() // 60)

        def bump(old):
            item = old if old and old["window"] == now else {"window": now, "count": 0}
            if item["count"] >= maximum:
                abort(429, "Too many requests; retry in a minute")
            item["count"] += 1
            return item

        store.mutate("limit:" + key, bump)

    @app.before_request
    def csrf():
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not session.get("csrf") or not secrets.compare_digest(
                request.headers.get("X-CSRF-Token", ""), session["csrf"]
            ):
                abort(403, "Refresh the page before submitting")

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; frame-ancestors 'none'"
        )
        return response

    @app.errorhandler(Exception)
    def error(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        app.logger.exception("Request failed")
        return jsonify(error="The service could not finish this request"), 500

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/health/ready")
    def health():
        store.healthy()
        if app.config.get("REDIS_URL"):
            import redis

            redis.Redis.from_url(app.config["REDIS_URL"], socket_timeout=3).ping()
        return jsonify(
            status="ready", storage="mongodb" if store.mongo is not None else "sqlite"
        )

    @app.get("/api/session")
    def current():
        session.setdefault("csrf", secrets.token_urlsafe(24))
        try:
            user = identity()
        except Exception:
            user = None
        return jsonify(username=user, csrf=session["csrf"])

    @app.post("/api/register")
    @app.post("/api/login")
    def login():
        limited("auth:" + request.remote_addr, 20)
        data = request.get_json() or {}
        name, password = (
            data.get("username", "").lower().strip(),
            data.get("password", ""),
        )
        if not re.fullmatch(r"[a-z0-9_]{3,30}", name) or not 10 <= len(password) <= 128:
            abort(
                400, "Use a 3–30 character username and a password of 10–128 characters"
            )
        if request.path.endswith("register"):
            password_hash = generate_password_hash(password)

            def register(old):
                if old:
                    abort(409, "Username unavailable")
                return {"username": name, "password_hash": password_hash, "rooms": []}

            store.mutate("user:" + name, register)
        user = store.get("user:" + name)
        if not user or not check_password_hash(user["password_hash"], password):
            abort(401, "Invalid credentials")
        session.clear()
        session.permanent = True
        session["token"], session["csrf"] = (
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(24),
        )
        store.put(
            "session:" + hashlib.sha256(session["token"].encode()).hexdigest(),
            {"username": name, "expires": time.time() + 7 * 86400},
        )
        return jsonify(username=name, csrf=session["csrf"])

    @app.post("/api/logout")
    def logout():
        identity()
        store.put(
            "session:" + hashlib.sha256(session["token"].encode()).hexdigest(),
            {"username": "", "expires": 0},
        )
        session.clear()
        return jsonify(ok=True)

    def room_for(room_id, user):
        room = store.get("room:" + room_id)
        if not room or user not in room["members"]:
            abort(404, "Room not found")
        return room

    def add_room(user, room_id):
        def change(old):
            old["rooms"] = list(dict.fromkeys(old["rooms"] + [room_id]))
            return old

        store.mutate("user:" + user, change)

    @app.get("/api/rooms")
    def rooms():
        user = identity()
        result = []
        for rid in store.get("user:" + user)["rooms"]:
            room = room_for(rid, user)
            result.append({k: room[k] for k in ("id", "name", "members", "sequence")})
        return jsonify(rooms=result)

    @app.post("/api/rooms")
    def create_room():
        user = identity()
        limited("rooms:" + user, 10)
        name = str((request.get_json() or {}).get("name", "")).strip()
        if not 1 <= len(name) <= 80:
            abort(400, "Room name must be 1–80 characters")
        if len(store.get("user:" + user)["rooms"]) >= 30:
            abort(409, "Room limit reached")
        rid = secrets.token_urlsafe(12)
        invite = secrets.token_urlsafe(24)
        store.put(
            "room:" + rid,
            {
                "id": rid,
                "name": name,
                "members": [user],
                "invite": invite,
                "sequence": 0,
                "messages": [],
            },
        )
        store.put("invite:" + invite, {"room": rid})
        add_room(user, rid)
        return jsonify(id=rid, invite=invite), 201

    @app.post("/api/join")
    def join():
        user = identity()
        limited("join:" + user, 20)
        invite = str((request.get_json() or {}).get("invite", ""))
        record = store.get("invite:" + invite)
        if not record:
            abort(404, "Invite not found")

        def change(room):
            if len(room["members"]) >= 50 and user not in room["members"]:
                abort(409, "Room is full")
            room["members"] = list(dict.fromkeys(room["members"] + [user]))
            return room

        store.mutate("room:" + record["room"], change)
        add_room(user, record["room"])
        return jsonify(id=record["room"])

    @app.get("/api/rooms/<rid>")
    def history(rid):
        room = room_for(rid, identity())
        after = request.args.get("after", 0, type=int)
        messages = room["messages"]
        return jsonify(
            id=rid,
            name=room["name"],
            invite=room["invite"],
            members=room["members"],
            messages=[m for m in messages if m["sequence"] > after],
            sequence=room["sequence"],
            truncated=bool(messages and after and after < messages[0]["sequence"] - 1),
        )

    @app.post("/api/rooms/<rid>/files")
    def upload(rid):
        user = identity()
        room_for(rid, user)
        limited("upload:" + user, 10)
        file = request.files.get("file")
        if not file:
            abort(400, "Choose a file")
        from werkzeug.utils import secure_filename

        name = secure_filename(file.filename or "")
        if Path(name).suffix.lower() not in {
            ".pdf",
            ".txt",
            ".csv",
            ".png",
            ".jpg",
            ".jpeg",
        }:
            abort(400, "Unsupported file type")
        data = file.read(2 * 1024 * 1024 + 1)
        if not data or len(data) > 2 * 1024 * 1024:
            abort(413, "Files must be between 1 byte and 2 MB")
        fid = secrets.token_urlsafe(16)
        store.put(
            "file:" + fid,
            {
                "room": rid,
                "name": name,
                "data": base64.b64encode(data).decode(),
                "owner": user,
            },
        )
        return jsonify(id=fid, name=name), 201

    @app.get("/api/files/<fid>")
    def download(fid):
        import io

        user = identity()
        file = store.get("file:" + fid)
        if not file:
            abort(404)
        room_for(file["room"], user)
        return send_file(
            io.BytesIO(base64.b64decode(file["data"])),
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=file["name"],
        )

    @app.post("/api/rooms/<rid>/translate")
    def translate(rid):
        import requests

        user = identity()
        room = room_for(rid, user)
        limited("translate:" + user, 10)
        payload = request.get_json() or {}
        message = next(
            (m for m in room["messages"] if m["id"] == payload.get("message_id")), None
        )
        if not message:
            abort(404, "Message not found")
        target = payload.get("target", "")
        if not re.fullmatch("[a-z]{2,3}", target):
            abort(400, "Use a language code such as en, es, or fr")
        url = os.environ.get("TRANSLATE_URL")
        if not url:
            abort(
                503, "Translation is not configured; original messages remain available"
            )
        try:
            response = requests.post(
                url,
                json={
                    "q": message["text"],
                    "source": "auto",
                    "target": target,
                    "api_key": os.environ.get("TRANSLATE_KEY", ""),
                },
                timeout=10,
            )
            response.raise_for_status()
            value = response.json().get("translatedText")
            if not isinstance(value, str) or len(value) > 10000:
                raise ValueError("Invalid translation")
        except (requests.RequestException, ValueError):
            abort(502, "Translation provider unavailable")
        return jsonify(text=value)

    @socket.on("connect")
    def connect(auth):
        try:
            identity()
            return bool(
                auth
                and secrets.compare_digest(
                    str(auth.get("csrf", "")), session.get("csrf", "missing")
                )
            )
        except Exception:
            return False

    @socket.on("join")
    def subscribe(data):
        try:
            room = room_for(str(data.get("room", "")), identity())
            join_room(room["id"])
            return {"ok": True, "sequence": room["sequence"]}
        except Exception:
            return {"ok": False, "error": "Room unavailable"}

    @socket.on("message")
    def message(data):
        try:
            user = identity()
            limited("message:" + user, 60)
            rid = str(data.get("room", ""))
            room_for(rid, user)
            client_id = str(data.get("client_id", ""))
            text = str(data.get("text", "")).strip()
            file_id = data.get("file_id")
            if (
                not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", client_id)
                or len(text) > 2000
                or not (text or file_id)
            ):
                return {"ok": False, "error": "Invalid message"}
            if file_id:
                file = store.get("file:" + str(file_id))
                if not file or file["room"] != rid or file["owner"] != user:
                    return {"ok": False, "error": "Invalid attachment"}
            key = user + ":" + client_id

            def append(room):
                previous = next((m for m in room["messages"] if m["id"] == key), None)
                if previous:
                    if previous["text"] != text or previous.get("file_id") != file_id:
                        abort(409, "Conflicting retry")
                    return room
                room["sequence"] += 1
                room["messages"].append(
                    {
                        "id": key,
                        "sequence": room["sequence"],
                        "sender": user,
                        "text": text,
                        "file_id": file_id,
                        "file_name": file["name"] if file_id else None,
                        "created": time.time(),
                    }
                )
                room["messages"] = room["messages"][-500:]
                return room

            room = store.mutate("room:" + rid, append)
            result = next(m for m in room["messages"] if m["id"] == key)
            socket.emit("message", {"room": rid, "message": result}, to=rid)
            return {"ok": True, "message": result}
        except Exception:
            return {
                "ok": False,
                "error": "Message could not be saved; check access or retry shortly",
            }

    return app
