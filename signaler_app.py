"""
Optional dedicated Socket.IO / WebRTC signaling service (same DB as client).

Deploy from the same repo as the client (Nexus-IQ). Railway start command example:
  gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT signaler_app:socketio

Set the same DATABASE_URL, CHAT_DB_ENCRYPTION_KEY, and CLIENT_SECRET_KEY as the client.
On the client service set SIGNALING_PUBLIC_URL=https://your-signal-service.up.railway.app
and SOCKETIO_CORS_ORIGINS=https://your-client.up.railway.app (or rely on CLIENT_PUBLIC_BASE_URL on client).
"""
import os

from dotenv import load_dotenv
from flask import Flask
from flask_socketio import SocketIO

load_dotenv()

from database_bootstrap import run_client_database_bootstrap
from db_config import configure_sqlalchemy
from models import db
from production_settings import apply_production_config
from public_urls import get_socketio_cors_origins
from realtime_handlers import TokenBackend, attach_realtime

signal_app = Flask(__name__)
apply_production_config(signal_app)
signal_app.config["SECRET_KEY"] = os.getenv("CLIENT_SECRET_KEY", "clientsecretkey123")
configure_sqlalchemy(signal_app)

_socketio_kw = {"cors_allowed_origins": get_socketio_cors_origins(), "async_mode": "eventlet"}
_mq = os.getenv("REDIS_URL") or os.getenv("SOCKETIO_MESSAGE_QUEUE")
if _mq:
    _socketio_kw["message_queue"] = _mq
socketio = SocketIO(signal_app, **_socketio_kw)

db.init_app(signal_app)
with signal_app.app_context():
    run_client_database_bootstrap(signal_app)

attach_realtime(socketio, signal_app, TokenBackend())
