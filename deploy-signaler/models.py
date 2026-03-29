from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import datetime
import os
from pathlib import Path
from sqlalchemy.types import TypeDecorator, Text
from sqlalchemy import text
from cryptography.fernet import Fernet, InvalidToken

db = SQLAlchemy()


class _EncryptionManager:
    """Centralized Fernet key loading/encryption helpers."""
    _KEY_ENV = "CHAT_DB_ENCRYPTION_KEY"
    _KEY_FILE = "chat_db_encryption.key"
    _instance = None

    def __init__(self):
        key = self._load_or_create_key()
        self._fernet = Fernet(key)

    def _load_or_create_key(self):
        env_key = os.getenv(self._KEY_ENV)
        if env_key:
            return env_key.encode("utf-8")

        # Persist a local key so restarts can still decrypt existing data.
        instance_dir = Path(__file__).resolve().parent / "instance"
        instance_dir.mkdir(exist_ok=True)
        key_path = instance_dir / self._KEY_FILE
        if key_path.exists():
            return key_path.read_bytes().strip()

        key = Fernet.generate_key()
        key_path.write_bytes(key)
        return key

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")

    @staticmethod
    def looks_encrypted(value: str) -> bool:
        return isinstance(value, str) and value.startswith("gAAAA")


class EncryptedText(TypeDecorator):
    """Transparent at-rest encryption for SQLAlchemy text columns."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if value == "":
            return value
        manager = _EncryptionManager.get()
        if manager.looks_encrypted(value):
            return value
        return manager.encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if value == "":
            return value
        manager = _EncryptionManager.get()
        if not manager.looks_encrypted(value):
            # Backward compatibility for plaintext rows before migration.
            return value
        try:
            return manager.decrypt(value)
        except InvalidToken:
            # If the wrong key is supplied, return raw value instead of crashing.
            return value

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    name = db.Column(EncryptedText, nullable=True) # Full Name (encrypted at rest)
    profile_pic_url = db.Column(EncryptedText, nullable=True) # Profile Picture (encrypted at rest)
    role = db.Column(db.String(50), nullable=False, default='user') # 'superadmin', 'admin', 'user'
    team_name = db.Column(db.String(100), nullable=True)
    team_role = db.Column(db.String(50), nullable=True) # 'member' or 'teamlead'
    designation = db.Column(db.String(50), nullable=True, default='SE') # 'SSE', 'SE', etc.
    is_restricted = db.Column(db.Boolean, default=False)
    dm_allowlist_only = db.Column(db.Boolean, default=False)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspace.id'), nullable=True)
    workspace = db.relationship('Workspace', backref=db.backref('users', foreign_keys=[workspace_id]), foreign_keys=[workspace_id])

class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(EncryptedText, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True) # null if channel
    channel_name = db.Column(db.String(100), nullable=True) # null if DM
    content = db.Column(EncryptedText, nullable=False)
    msg_type = db.Column(db.String(50), default='text') # 'text', 'audio', 'video'
    file_path = db.Column(EncryptedText, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

    sender = db.relationship('User', foreign_keys=[sender_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])

class Channel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False) # Internal slug/ID
    display_name = db.Column(db.String(255), nullable=True) # Rich name with Emojis
    icon_url = db.Column(db.String(500), nullable=True) # Profile pic
    team_name = db.Column(db.String(100), nullable=False)
    visibility = db.Column(db.String(50), nullable=False, default='all') # 'all', 'se_tl', etc.
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspace.id'), nullable=True)
    post_permission_mode = db.Column(db.String(50), nullable=False, default='all_visible') # 'all_visible' or 'custom'
    is_private_group = db.Column(db.Boolean, nullable=False, default=False)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)
    type = db.Column(db.String(50), default='mention') # 'mention' or 'all'
    is_seen = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class ChannelVisit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    channel_name = db.Column(db.String(100), nullable=False)
    last_visit = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspace.id'), nullable=True)
    can_deploy_publicly = db.Column(db.Boolean, default=False)

class DMPermission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Workspace(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, default='Entegrasources')
    logo_url = db.Column(db.String(500), nullable=True, default='/static/img/entegrasources_logo.png')
    theme_color = db.Column(db.String(50), nullable=False, default='#7161d4')
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    is_private = db.Column(db.Boolean, default=True) # Initially private
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    creator = db.relationship('User', foreign_keys=[creator_id])
    allow_group_creation = db.Column(db.Boolean, default=True)

class GroupMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('channel.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    user = db.relationship('User', foreign_keys=[user_id])
    group = db.relationship('Channel', foreign_keys=[group_id])

    group = db.relationship('Channel', backref=db.backref('members', lazy='dynamic'))
    user = db.relationship('User')

class ChannelRolePermission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channel.id'), nullable=False)
    team_role = db.Column(db.String(50), nullable=False) # 'teamlead' or 'member'
    added_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    channel = db.relationship('Channel', backref=db.backref('role_permissions', lazy='dynamic', cascade='all, delete-orphan'))

class WorkspaceAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspace.id'), nullable=False)


def migrate_encrypted_fields():
    """
    One-time best-effort migration: convert legacy plaintext values into encrypted values.
    """
    changed = False

    manager = _EncryptionManager.get()
    users = User.query.all()
    for u in users:
        if u.name and not manager.looks_encrypted(u.name):
            u.name = manager.encrypt(u.name)
            changed = True
        if u.profile_pic_url and not manager.looks_encrypted(u.profile_pic_url):
            u.profile_pic_url = manager.encrypt(u.profile_pic_url)
            changed = True

    logs = Log.query.all()
    for l in logs:
        if l.action and not manager.looks_encrypted(l.action):
            l.action = manager.encrypt(l.action)
            changed = True

    messages = Message.query.all()
    for m in messages:
        if m.content and not manager.looks_encrypted(m.content):
            m.content = manager.encrypt(m.content)
            changed = True
        if m.file_path and not manager.looks_encrypted(m.file_path):
            m.file_path = manager.encrypt(m.file_path)
            changed = True

    if changed:
        db.session.commit()


def ensure_performance_indexes():
    """
    Create low-risk indexes used by hot read paths.
    """
    statements = [
        "CREATE INDEX IF NOT EXISTS idx_message_receiver_is_read ON message(receiver_id, is_read)",
        "CREATE INDEX IF NOT EXISTS idx_message_channel_timestamp ON message(channel_name, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_message_sender_receiver_timestamp ON message(sender_id, receiver_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_message_receiver_sender_timestamp ON message(receiver_id, sender_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_notification_user_timestamp ON notification(user_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_channel_visit_user_channel ON channel_visit(user_id, channel_name)",
        "CREATE INDEX IF NOT EXISTS idx_workspace_access_user_workspace ON workspace_access(user_id, workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_workspace_access_workspace_user ON workspace_access(workspace_id, user_id)",
        "CREATE INDEX IF NOT EXISTS idx_group_member_group_user ON group_member(group_id, user_id)",
        "CREATE INDEX IF NOT EXISTS idx_channel_role_channel_role ON channel_role_permission(channel_id, team_role)",
        "CREATE INDEX IF NOT EXISTS idx_channel_workspace_name ON channel(workspace_id, name)",
        "CREATE INDEX IF NOT EXISTS idx_channel_workspace_private ON channel(workspace_id, is_private_group)",
        "CREATE INDEX IF NOT EXISTS idx_user_workspace ON user(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_team_name ON user(team_name)",
    ]
    for stmt in statements:
        db.session.execute(text(stmt))
    db.session.commit()

