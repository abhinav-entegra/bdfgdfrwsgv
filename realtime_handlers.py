"""Socket.IO signaling (calls, huddles, presence) — shared by client app and optional signaling service."""
from flask import request
from flask_login import current_user
from flask_socketio import SocketIO, disconnect, emit, join_room

from chat_policy import can_user_dm_target, can_user_view_channel, get_channel_in_context
from models import User, db
from presence_store import mark_offline as presence_mark_offline
from presence_store import mark_online as presence_mark_online

active_users = set()


def _resolve_socket_target_id(data):
    raw = (data or {}).get("to")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class FlaskSessionBackend:
    """Same-origin Socket.IO: authenticate via Flask-Login session cookie."""

    def on_connect(self, auth=None):
        return current_user if getattr(current_user, "is_authenticated", False) else None

    def on_disconnect(self):
        pass

    def current_user(self):
        return current_user if getattr(current_user, "is_authenticated", False) else None


class TokenBackend:
    """Cross-origin signaling: browser sends signed token from /api/socket_token."""

    def __init__(self):
        self._sid_uid = {}

    def on_connect(self, auth=None):
        from socket_auth import verify_socket_token

        token = (auth or {}).get("token") if isinstance(auth, dict) else None
        uid = verify_socket_token(token) if token else None
        if not uid:
            return None
        self._sid_uid[request.sid] = uid
        return db.session.get(User, uid)

    def on_disconnect(self):
        self._sid_uid.pop(request.sid, None)

    def current_user(self):
        uid = self._sid_uid.get(request.sid)
        return db.session.get(User, uid) if uid else None


def attach_realtime(socketio: SocketIO, app, backend) -> None:
    def require_user():
        user = backend.current_user()
        if not user or not getattr(user, "is_authenticated", True):
            disconnect()
            return None
        return user

    @socketio.on("connect")
    def handle_connect(auth=None):
        user = backend.on_connect(auth)
        if not user:
            return False
        active_users.add(user.id)
        presence_mark_online(user.id)
        emit("user_status_change", {"user_id": user.id, "status": "online"}, broadcast=True)

    @socketio.on("disconnect")
    def handle_disconnect():
        user = backend.current_user()
        uid = user.id if user else None
        backend.on_disconnect()
        if uid is not None:
            active_users.discard(uid)
            presence_mark_offline(uid)
            emit("user_status_change", {"user_id": uid, "status": "offline"}, broadcast=True)

    @socketio.on("join")
    def on_join(data):
        u = require_user()
        if not u:
            return
        room = f"user_{u.id}"
        join_room(room)
        app.logger.debug("User joined personal signalling room: %s", room)

    @socketio.on("call-user")
    def handle_call(data):
        u = require_user()
        if not u:
            return
        target_id = _resolve_socket_target_id(data)
        if not target_id:
            return
        target = db.session.get(User, target_id)
        if not target or not can_user_dm_target(u, target):
            return
        offer = data.get("offer")
        call_type = data.get("type")
        room = f"user_{target_id}"
        emit(
            "incoming-call",
            {"from": u.id, "from_email": u.email, "offer": offer, "type": call_type},
            room=room,
        )

    @socketio.on("answer-call")
    def handle_answer(data):
        u = require_user()
        if not u:
            return
        target_id = _resolve_socket_target_id(data)
        if not target_id:
            return
        target = db.session.get(User, target_id)
        if not target or not can_user_dm_target(u, target):
            return
        answer = data.get("answer")
        room = f"user_{target_id}"
        emit("call-answered", {"from": u.id, "answer": answer}, room=room)

    @socketio.on("ice-candidate")
    def handle_ice(data):
        u = require_user()
        if not u:
            return
        target_id = _resolve_socket_target_id(data)
        if not target_id:
            return
        target = db.session.get(User, target_id)
        if not target or not can_user_dm_target(u, target):
            return
        candidate = data.get("candidate")
        room = f"user_{target_id}"
        emit("ice-candidate", {"from": u.id, "candidate": candidate}, room=room)

    @socketio.on("join-huddle")
    def on_join_huddle(data):
        u = require_user()
        if not u:
            return
        room_name = data.get("room_name")
        channel = get_channel_in_context(u, channel_name=room_name) if room_name else None
        if channel and can_user_view_channel(u, channel):
            room = f"huddle_{room_name}"
            join_room(room)
            app.logger.debug("User %s joined huddle room %s", u.email, room)
            emit(
                "huddle-status-update",
                {
                    "user": u.email,
                    "user_id": u.id,
                    "action": "joined",
                    "audio": data.get("audio", True),
                    "video": data.get("video", False),
                },
                room=room,
            )

    @socketio.on("huddle-action")
    def on_huddle_action(data):
        u = require_user()
        if not u:
            return
        room_name = data.get("room_name")
        channel = get_channel_in_context(u, channel_name=room_name) if room_name else None
        if not channel or not can_user_view_channel(u, channel):
            return
        action = data.get("action")
        room = f"huddle_{room_name}"
        app.logger.debug("Huddle action [%s] from %s in room %s", action, u.email, room)
        emit(
            "huddle-status-update",
            {
                "user": u.email,
                "user_id": u.id,
                "action": action,
                "audio": data.get("audio"),
                "video": data.get("video"),
            },
            room=room,
        )

    @socketio.on("huddle-signal")
    def on_huddle_signal(data):
        u = require_user()
        if not u:
            return
        target_id = _resolve_socket_target_id(data)
        if not target_id:
            return
        target = db.session.get(User, target_id)
        if not target or not can_user_dm_target(u, target):
            return
        room = f"user_{target_id}"
        app.logger.debug("Huddle signal from %s -> Room %s", u.email, room)
        emit(
            "huddle-signal",
            {"from": u.id, "from_email": u.email, "signal": data.get("signal")},
            room=room,
        )

    @socketio.on("end-call")
    def handle_end(data):
        u = require_user()
        if not u:
            return
        target_id = _resolve_socket_target_id(data)
        if not target_id:
            return
        target = db.session.get(User, target_id)
        if not target or not can_user_dm_target(u, target):
            return
        room = f"user_{target_id}"
        emit("call-ended", {"from": u.id}, room=room)
