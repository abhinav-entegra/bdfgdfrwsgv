"""Short-lived signed tokens so the browser can connect Socket.IO to a separate signaling host."""
import os

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SOCKET_SALT = "nexus-socket-v1"
_MAX_AGE_SEC = 15 * 60


def _serializer():
    secret = os.getenv("CLIENT_SECRET_KEY", "clientsecretkey123")
    return URLSafeTimedSerializer(secret, salt=_SOCKET_SALT)


def create_socket_token(user_id: int) -> str:
    return _serializer().dumps({"uid": int(user_id)})


def verify_socket_token(token: str | None, max_age: int = _MAX_AGE_SEC) -> int | None:
    if not token or not isinstance(token, str):
        return None
    try:
        data = _serializer().loads(token, max_age=max_age)
        return int(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None
