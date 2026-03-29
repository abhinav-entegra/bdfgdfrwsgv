import os
import uuid

from dotenv import load_dotenv
from urllib.parse import urlparse

load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO
from sqlalchemy import or_, text, and_, func
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash
from models import db, User, Message, Channel, Notification, ChannelVisit, DMPermission, Workspace, WorkspaceAccess, Team, GroupMember, ChannelRolePermission
from db_config import configure_sqlalchemy
from database_bootstrap import run_client_database_bootstrap
from admin_blueprint import admin_bp, create_initial_admin
from legacy_sqlite_import import try_auto_import_legacy
from socket_auth import create_socket_token
from production_settings import apply_production_config
from public_urls import (
    get_signaling_public_base_url,
    get_socketio_cors_origins,
)
from chat_policy import (
    apply_channel_visibility_filter,
    can_user_dm_target,
    can_user_view_channel,
    get_channel_base_query,
    get_channel_bulk_roles,
    get_channel_explicit_member_ids,
    get_channel_in_context,
    is_channel_manager,
    is_public_ecosystem_workspace,
)
import datetime

from realtime_handlers import FlaskSessionBackend, active_users, attach_realtime

app = Flask(__name__)
apply_production_config(app)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or os.getenv('CLIENT_SECRET_KEY') or os.getenv('ADMIN_SECRET_KEY') or 'dev-unified-secret'

# Standalone unified app: all persistence is SQLite in this single file (DATABASE_URL is ignored).
_unified_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unified_chat.db")
os.environ.pop("DATABASE_URL", None)
configure_sqlalchemy(app)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + _unified_db_path.replace("\\", "/")
app.config['SESSION_COOKIE_NAME'] = 'nexus_session'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

_socketio_kw = {"cors_allowed_origins": get_socketio_cors_origins(), "async_mode": "eventlet"}
_mq = os.getenv("REDIS_URL") or os.getenv("SOCKETIO_MESSAGE_QUEUE")
if _mq:
    _socketio_kw["message_queue"] = _mq
socketio = SocketIO(app, **_socketio_kw)

UPLOAD_FOLDER = 'static/uploads/voice'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db.init_app(app)
with app.app_context():
    run_client_database_bootstrap(app)
    # Import old chat.db / LEGACY_SQLITE_FILE before seeding so PKs and users survive.
    try_auto_import_legacy(os.path.dirname(os.path.abspath(__file__)), _unified_db_path)
    create_initial_admin()

app.register_blueprint(admin_bp)

login_manager = LoginManager(app)
login_manager.login_view = 'login_client'


def _safe_next_redirect(target):
    if not target or not isinstance(target, str):
        return None
    p = urlparse(target)
    if p.scheme not in ('http', 'https'):
        return None
    base = urlparse(request.host_url)
    if p.netloc != base.netloc:
        return None
    return target


@login_manager.unauthorized_handler
def _handle_unauthorized():
    if request.path.startswith('/admin'):
        return redirect(url_for('login_admin', next=request.url))
    return redirect(url_for('login_client', next=request.url))


@app.route("/healthz", methods=["GET"])
def healthz():
    """Lightweight probe for load balancers (Railway, etc.)."""
    return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


attach_realtime(socketio, app, FlaskSessionBackend())

def user_is_teamlead_from_public_ecosystem(user):
    if (user.team_role or '').strip().lower() != 'teamlead':
        return False
    ws = db.session.get(Workspace, user.workspace_id)
    return is_public_ecosystem_workspace(ws)

def can_manage_public_group_post_policy(user, workspace=None):
    ws = workspace or db.session.get(Workspace, user.workspace_id)
    if not is_public_ecosystem_workspace(ws):
        return False
    if (user.team_role or '').strip().lower() != 'teamlead':
        return False
    tn = (user.team_name or '').strip().lower()
    return ('espl' in tn) or ('admin apple' in tn)

def get_workspace_access_user_ids(workspace_id):
    access_ids = [wa.user_id for wa in WorkspaceAccess.query.filter_by(workspace_id=workspace_id).all()]
    ws = db.session.get(Workspace, workspace_id)
    if ws and ws.creator_id and ws.creator_id not in access_ids:
        access_ids.append(ws.creator_id)
    return access_ids

def get_context_member_query(user, workspace):
    if not workspace:
        return User.query.filter(User.id == None)

    access_ids = get_workspace_access_user_ids(workspace.id)
    query = User.query.filter(User.id.in_(access_ids))

    # Team ecosystems stay team-scoped, while public ecosystems show everyone in that ecosystem.
    if workspace.id == 1 or workspace.is_private:
        if user.team_name:
            query = query.filter(User.team_name == user.team_name)
    return query

def get_context_members(user, workspace, search_term=None, limit=None):
    query = get_context_member_query(user, workspace)

    if search_term:
        pattern = f"%{search_term}%"
        query = query.filter(or_(User.email.ilike(pattern), User.name.ilike(pattern)))

    query = query.order_by(User.name.asc(), User.email.asc())
    if limit:
        query = query.limit(limit)
    return query.all()


def get_ecosystem_member_query(workspace):
    if not workspace:
        return User.query.filter(User.id == None)
    access_ids = get_workspace_access_user_ids(workspace.id)
    if not access_ids:
        return User.query.filter(User.id == None)
    return User.query.filter(User.id.in_(access_ids))


def get_ecosystem_members(workspace, search_term=None, limit=None):
    query = get_ecosystem_member_query(workspace)
    if search_term:
        pattern = f"%{search_term}%"
        query = query.filter(or_(User.email.ilike(pattern), User.name.ilike(pattern)))
    query = query.order_by(User.name.asc(), User.email.asc())
    if limit:
        query = query.limit(limit)
    return query.all()


def get_manageable_members_for_teamlead_scope(actor, workspace, search_term=None, limit=None):
    # The user requested that anyone in the ecosystem can be added to groups.
    # So we provide the full ecosystem member list.
    return get_ecosystem_members(workspace, search_term=search_term, limit=limit)


def can_manage_target_user(actor, target):
    if not target:
        return False
    if actor.role == 'superadmin':
        return True
    if actor.role == 'admin':
        return target.workspace_id == actor.workspace_id
    if (actor.team_role or '').strip().lower() == 'teamlead':
        return (
            target.workspace_id == actor.workspace_id and
            (target.team_name or '') == (actor.team_name or '')
        )
    return False


def can_user_post_to_channel(user, channel):
    if not can_user_view_channel(user, channel):
        return False

    if user.role in ['admin', 'superadmin']:
        return True

    if channel.post_permission_mode != 'custom':
        return True

    if user.id in get_channel_explicit_member_ids(channel):
        return True

    if user.team_role and user.team_role in get_channel_bulk_roles(channel):
        return True

    return False

def get_channel_post_block_reason(user, channel):
    if user.is_restricted:
        return 'Your communication privileges have been revoked.'
    if channel.post_permission_mode == 'custom':
        return 'You can view this group, but only selected members can send messages here.'
    return 'You do not have permission to send messages in this group.'

def get_channel_member_records(channel, viewer):
    workspace = db.session.get(Workspace, channel.workspace_id)
    if not workspace:
        return []

    base_members = get_ecosystem_members(workspace) if bool(getattr(channel, 'is_private_group', False)) else get_context_members(viewer, workspace)
    visible_users = [
        member for member in base_members
        if can_user_view_channel(member, channel)
    ]

    explicit_member_ids = get_channel_explicit_member_ids(channel)
    bulk_roles = get_channel_bulk_roles(channel)
    use_custom_permissions = channel.post_permission_mode == 'custom'

    records = []
    for member in visible_users:
        if not use_custom_permissions:
            records.append({
                'user': member,
                'source': 'workspace',
                'removable': False
            })
            continue

        source = None
        removable = False
        if member.id in explicit_member_ids:
            source = 'member'
            removable = member.role not in ['admin', 'superadmin']
        elif member.team_role and member.team_role in bulk_roles:
            source = 'member_bulk' if member.team_role == 'member' else member.team_role
        elif member.role in ['admin', 'superadmin']:
            source = 'admin'

        if source:
            records.append({
                'user': member,
                'source': source,
                'removable': removable
            })

    return records

@app.route('/')
def index():
    can_admin = current_user.is_authenticated and current_user.role in ('admin', 'superadmin')
    return render_template(
        'portal_hub.html',
        is_authenticated=current_user.is_authenticated,
        can_admin_console=can_admin,
    )


@app.route('/login/client', methods=['GET', 'POST'])
def login_client():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            nxt = request.args.get('next') or request.form.get('next')
            if nxt and _safe_next_redirect(nxt):
                return redirect(nxt)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('client_login.html', admin_portal_url=None)


@app.route('/login/admin', methods=['GET', 'POST'])
def login_admin():
    if current_user.is_authenticated:
        if current_user.role in ('admin', 'superadmin'):
            return redirect(url_for('admin.admin_dashboard'))
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            if user.role not in ('admin', 'superadmin'):
                flash(
                    'This sign-in is for administrator accounts. Use Client sign-in for workspace access.',
                    'error',
                )
                return redirect(url_for('login_client'))
            login_user(user)
            nxt = request.args.get('next') or request.form.get('next')
            if nxt and _safe_next_redirect(nxt):
                return redirect(nxt)
            return redirect(url_for('admin.admin_dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('login.html', client_portal_url=None)


@app.route('/login', methods=['GET', 'POST'])
def unified_login():
    """Legacy URL: send users to the portal first."""
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Fetch team deployment rights
    team_rights = False
    if current_user.team_name:
        team = Team.query.filter_by(name=current_user.team_name).first()
        if team:
            team_rights = team.can_deploy_publicly or False
            
    ws = current_user.workspace # Assuming current_user.workspace is the Workspace object
    sig = get_signaling_public_base_url()
    return render_template(
        "client_dashboard.html",
        workspace=ws,
        curr_team=current_user.team_name,
        can_deploy_publicly=team_rights,
        can_manage_public_group_policy=can_manage_public_group_post_policy(current_user, ws),
        signaling_public_url=sig,
        socket_bootstrap_token=create_socket_token(current_user.id) if sig else "",
    )

@app.route('/logout')
@login_required
def unified_logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/api/switch_ecosystem', methods=['POST'])
@login_required
def switch_ecosystem():
    data = request.json
    ws_id = data.get('workspace_id')
    ws = db.session.get(Workspace, ws_id)
    if not ws:
        return jsonify({'error': 'Workspace not found'}), 404

    has_access = WorkspaceAccess.query.filter_by(user_id=current_user.id, workspace_id=ws_id).first()
    if ws.is_private and not has_access and current_user.role not in ['admin', 'superadmin']:
        return jsonify({'error': 'Access denied'}), 403
    
    current_user.workspace_id = ws_id
    db.session.commit()
    return jsonify({'success': True})

# --- CHAT API ENDPOINTS ---

@app.route('/api/get_team_members')
@login_required
def get_team_members():
    ws = db.session.get(Workspace, current_user.workspace_id)
    members = get_context_members(current_user, ws)
    
    return jsonify([{
        'id': m.id,
        'email': m.email,
        'name': m.name or m.email.split('@')[0],
        'profile_pic_url': m.profile_pic_url or f"https://ui-avatars.com/api/?name={m.name or m.email.split('@')[0]}&background=ffffff&color=111827",
        'role': m.role,
        'team_role': m.team_role,
        'designation': m.designation or 'SE',
        'is_me': m.id == current_user.id,
        'is_restricted': m.is_restricted,
        'dm_allowlist_only': bool(getattr(m, 'dm_allowlist_only', False)),
        'workspace_id': m.workspace_id,
    } for m in members])

@app.route("/api/socket_token", methods=["GET"])
@login_required
def api_socket_token():
    return jsonify(token=create_socket_token(current_user.id))


@app.route('/api/get_online_users')
@login_required
def get_online_users():
    from presence_store import list_online_ids

    return jsonify(list_online_ids(active_users))

@app.route('/api/get_cross_ecosystem_dms')
@login_required
def get_cross_ecosystem_dms():
    # Find all people I've had DMs with
    sent = db.session.query(Message.receiver_id).filter(Message.sender_id == current_user.id, Message.channel_name == None).distinct().all()
    rec = db.session.query(Message.sender_id).filter(Message.receiver_id == current_user.id, Message.channel_name == None).distinct().all()
    
    unique_ids = set([r[0] for r in sent if r[0]] + [r[0] for r in rec if r[0]])
    unique_ids.discard(current_user.id)
    
    if not unique_ids:
        return jsonify([])
        
    # Find who is NOT in the current workspace
    current_ws_access = WorkspaceAccess.query.filter_by(workspace_id=current_user.workspace_id).all()
    active_ids = set([wa.user_id for wa in current_ws_access])
    
    other_ids = [i for i in unique_ids if i not in active_ids]
    if not other_ids:
        return jsonify([])
        
    members = User.query.filter(User.id.in_(other_ids)).all()
    
    return jsonify([{
        'id': m.id,
        'email': m.email,
        'name': m.name or m.email.split('@')[0],
        'profile_pic_url': m.profile_pic_url or f"https://ui-avatars.com/api/?name={m.name or m.email.split('@')[0]}&background=ffffff&color=111827",
        'role': m.role,
        'team_role': m.team_role,
        'designation': m.designation or 'SE',
        'is_me': False,
        'is_external': True, # Flag to show they are from other ecosystems
        'is_restricted': m.is_restricted
    } for m in members])

@app.route('/api/get_dm_permissions')
@login_required
def get_dm_permissions():
    user_id = request.args.get('user_id')
    if not user_id: return jsonify([])
    target_user = db.session.get(User, int(user_id)) if str(user_id).isdigit() else None
    if not can_manage_target_user(current_user, target_user):
        return jsonify({'error': 'Unauthorized'}), 403

    perms = DMPermission.query.filter_by(user_id=int(user_id)).all()
    return jsonify([p.target_id for p in perms])

@app.route('/api/update_dm_permission', methods=['POST'])
@login_required
def update_dm_permission():
    if current_user.team_role != 'teamlead' and current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.json
    user_id = data.get('user_id')
    target_id = data.get('target_id')
    allowed = data.get('allowed') # bool

    if not str(user_id).isdigit() or not str(target_id).isdigit():
        return jsonify({'error': 'Invalid user id'}), 400
    source_user = db.session.get(User, int(user_id))
    target_user = db.session.get(User, int(target_id))
    if not can_manage_target_user(current_user, source_user) or not can_manage_target_user(current_user, target_user):
        return jsonify({'error': 'Unauthorized'}), 403

    if allowed:
        existing = DMPermission.query.filter_by(user_id=int(user_id), target_id=int(target_id)).first()
        if not existing:
            db.session.add(DMPermission(user_id=int(user_id), target_id=int(target_id)))
    else:
        DMPermission.query.filter_by(user_id=int(user_id), target_id=int(target_id)).delete()
    
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/toggle_user_restriction', methods=['POST'])
@login_required
def toggle_user_restriction():
    if current_user.team_role != 'teamlead' and current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.json
    user_id = data.get('user_id')
    restricted = data.get('restricted', False) # bool
    
    if not str(user_id).isdigit():
        return jsonify({'error': 'Invalid user id'}), 400
    target = User.query.get(int(user_id))
    if not can_manage_target_user(current_user, target):
        return jsonify({'error': 'Unauthorized'}), 403
    if target:
        target.is_restricted = restricted
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/set_dm_allowlist_only', methods=['POST'])
@login_required
def set_dm_allowlist_only():
    if current_user.team_role != 'teamlead' and current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.json
    user_id = data.get('user_id')
    enabled = bool(data.get('enabled', False))
    if not str(user_id).isdigit():
        return jsonify({'error': 'Invalid user id'}), 400
    target = User.query.get(int(user_id))
    if not can_manage_target_user(current_user, target):
        return jsonify({'error': 'Unauthorized'}), 403
    if not target:
        return jsonify({'error': 'User not found'}), 404
    target.dm_allowlist_only = enabled
    db.session.commit()
    return jsonify({'success': True, 'dm_allowlist_only': enabled})

@app.route('/api/send_message', methods=['POST'])
@login_required
def send_message():
    data = request.json
    content = data.get('content')
    receiver_id = data.get('receiver_id') # For DMs
    channel_name = data.get('channel_name') # For Channels
    msg_type = data.get('type', 'text')
    file_path = data.get('file_path', None)

    if not content and msg_type == 'text':
        return jsonify({'error': 'No content'}), 400

    # NEW COMM RESTRICTIONS
    if current_user.is_restricted:
        return jsonify({'error': 'Your communication privileges have been revoked.'}), 403

    # Permission Check for DMs
    if receiver_id:
        rid = int(receiver_id)
        target = User.query.get(rid)
        if not target: return jsonify({'error': 'User not found'}), 404
        if not can_user_dm_target(current_user, target):
            if bool(getattr(current_user, 'dm_allowlist_only', False)):
                return jsonify({'error': 'You may only message people your team lead has selected, or team leads.'}), 403
            return jsonify({'error': 'You can only message team leads or members your team lead has approved for DMs.'}), 403
    elif channel_name:
        channel = get_channel_in_context(current_user, channel_name=channel_name)
        if not channel:
            return jsonify({'error': 'Group not found'}), 404
        if not can_user_post_to_channel(current_user, channel):
            return jsonify({'error': get_channel_post_block_reason(current_user, channel)}), 403

    new_msg = Message(
        sender_id=current_user.id,
        receiver_id=int(receiver_id) if receiver_id else None,
        channel_name=channel_name,
        content=content or "",
        msg_type=msg_type,
        file_path=file_path,
        timestamp=datetime.datetime.utcnow()
    )
    db.session.add(new_msg)
    db.session.commit()

    # TAGGING LOGIC
    if channel_name and content:
        channel = get_channel_in_context(current_user, channel_name=channel_name)
        # Check for @all
        if channel and '@all' in content.lower():
            channel_users = [record['user'] for record in get_channel_member_records(channel, current_user)]
            for u in channel_users:
                if u.id != current_user.id:
                    existing_notif = Notification.query.filter_by(user_id=u.id, message_id=new_msg.id, type='all').first()
                    if not existing_notif:
                        notif = Notification(user_id=u.id, message_id=new_msg.id, type='all')
                        db.session.add(notif)
        
        # Check for specific @mentions
        import re
        mentions = re.findall(r'@([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6})', content)
        for email_to_find in mentions:
            tagged_user = User.query.filter_by(email=email_to_find).first()
            if tagged_user and tagged_user.id != current_user.id:
                existing_notif = Notification.query.filter_by(user_id=tagged_user.id, message_id=new_msg.id, type='mention').first()
                if not existing_notif:
                    notif = Notification(user_id=tagged_user.id, message_id=new_msg.id, type='mention')
                    db.session.add(notif)
        db.session.commit()

    return jsonify({'success': True, 'msg_id': new_msg.id})

@app.route('/api/get_messages')
@login_required
def get_messages():
    receiver_id = request.args.get('receiver_id')
    channel_name = request.args.get('channel_name')

    if channel_name:
        channel = get_channel_in_context(current_user, channel_name=channel_name)
        if not channel or not can_user_view_channel(current_user, channel):
            return jsonify({'error': 'Group not found'}), 404
        # Join with User to ensure we are only getting messages from the same workspace
        msgs = Message.query.join(User, Message.sender_id == User.id).filter(
            Message.channel_name == channel_name,
            User.workspace_id == current_user.workspace_id
        ).order_by(Message.timestamp.asc()).all()
        # Track visit for notification clearing
        visit = ChannelVisit.query.filter_by(user_id=current_user.id, channel_name=channel_name).first()
        if not visit:
            visit = ChannelVisit(user_id=current_user.id, channel_name=channel_name)
            db.session.add(visit)
        visit.last_visit = datetime.datetime.utcnow()
        db.session.commit()
        print(f">> Channel {channel_name} marked as seen for {current_user.email} (WS: {current_user.workspace_id})")
    elif receiver_id:
        rid = int(receiver_id)
        # Get messages between current_user and receiver_id
        msgs = Message.query.filter(
            ((Message.sender_id == current_user.id) & (Message.receiver_id == rid)) |
            ((Message.sender_id == rid) & (Message.receiver_id == current_user.id))
        ).order_by(Message.timestamp.asc()).all()
        # Mark as read
        for m in msgs:
            if m.receiver_id == current_user.id:
                m.is_read = True
        db.session.commit()
        print(f">> DM with User ID {rid} marked as read for {current_user.email}")
    else:
        return jsonify([])

    return jsonify([{
        'sender_email': m.sender.email if m.sender else "Unknown",
        'sender_name': m.sender.name if m.sender else (m.sender.email.split('@')[0] if m.sender else "Unknown"),
        'sender_id': m.sender_id,
        'sender_pic': m.sender.profile_pic_url if m.sender else None,
        'content': m.content,
        'type': m.msg_type,
        'file_path': m.file_path,
        'timestamp': m.timestamp.strftime('%I:%M %p'),
        'is_me': m.sender_id == current_user.id,
        'is_read': m.is_read
    } for m in msgs])

@app.route('/api/get_unread_counts')
@login_required
def get_unread_counts():
    counts = db.session.query(Message.sender_id, db.func.count(Message.id)).filter(
        Message.receiver_id == current_user.id,
        Message.is_read == False
    ).group_by(Message.sender_id).all()
    
    return jsonify({sender_id: count for sender_id, count in counts})

@app.route('/api/get_channel_unread')
@login_required
def get_channel_unread():
    channels = apply_channel_visibility_filter(get_channel_base_query(current_user), current_user).all()
    channel_names = [ch.name for ch in channels]
    res = {name: 0 for name in channel_names}
    if not channel_names:
        return jsonify(res)

    default_visit_time = datetime.datetime(2000, 1, 1)
    counts = (
        db.session.query(Message.channel_name, func.count(Message.id))
        .join(User, Message.sender_id == User.id)
        .outerjoin(
            ChannelVisit,
            and_(
                ChannelVisit.user_id == current_user.id,
                ChannelVisit.channel_name == Message.channel_name
            )
        )
        .filter(
            Message.channel_name.in_(channel_names),
            User.workspace_id == current_user.workspace_id,
            Message.timestamp > func.coalesce(ChannelVisit.last_visit, default_visit_time)
        )
        .group_by(Message.channel_name)
        .all()
    )
    for channel_name, count in counts:
        res[channel_name] = count
    return jsonify(res)

@app.route('/api/get_activity')
@login_required
def get_activity():
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.timestamp.desc()).limit(20).all()
    message_ids = [n.message_id for n in notifs]
    messages_by_id = {}
    if message_ids:
        messages = (
            Message.query.options(joinedload(Message.sender))
            .filter(Message.id.in_(message_ids))
            .all()
        )
        messages_by_id = {m.id: m for m in messages}
    res = []
    for n in notifs:
        msg = messages_by_id.get(n.message_id)
        res.append({
            'id': n.id,
            'type': n.type,
            'is_seen': n.is_seen,
            'sender': msg.sender.email if msg and msg.sender else "Unknown",
            'sender_id': msg.sender_id if msg else None,
            'channel_name': msg.channel_name if msg else None,
            'content': msg.content[:50] if msg else "",
            'time': n.timestamp.strftime('%I:%M %p')
        })
    return jsonify(res)

@app.route('/api/mark_activity_read', methods=['POST'])
@login_required
def mark_activity_read():
    Notification.query.filter_by(user_id=current_user.id).update({'is_seen': True})
    db.session.commit()
    return jsonify({'success': True})

# --- CHANNEL API ---
@app.route('/api/get_channels')
@login_required
def get_channels():
    channels = apply_channel_visibility_filter(get_channel_base_query(current_user), current_user).filter(
        (Channel.is_private_group == False) | (Channel.is_private_group == None)
    ).all()
    ws = db.session.get(Workspace, current_user.workspace_id)
    is_public = is_public_ecosystem_workspace(ws)
    can_manage_public = can_manage_public_group_post_policy(current_user, ws) if is_public else False
    return jsonify([{
        'id': c.id, 
        'name': c.name, 
        'display_name': c.display_name or c.name,
        'icon_url': c.icon_url or f"https://ui-avatars.com/api/?name={c.name}&background=ffffff&color=111827",
        'visibility': c.visibility,
        'post_permission_mode': c.post_permission_mode or 'all_visible',
        'can_post': can_user_post_to_channel(current_user, c),
        'workspace_is_public_ecosystem': is_public,
        'can_manage_public_post_policy': can_manage_public
    } for c in channels])

@app.route('/api/get_groups')
@login_required
def get_groups():
    # Private groups are only supported in Team Ecosystem (workspace 1).
    if current_user.workspace_id != 1:
        return jsonify([])
    groups = get_channel_base_query(current_user).filter(Channel.is_private_group == True).all()
    groups = [g for g in groups if can_user_view_channel(current_user, g)]
    return jsonify([{
        'id': g.id,
        'name': g.name,
        'display_name': g.display_name or g.name,
        'icon_url': g.icon_url or f"https://ui-avatars.com/api/?name={g.name}&background=ffffff&color=111827",
        'post_permission_mode': g.post_permission_mode or 'custom',
        'can_post': can_user_post_to_channel(current_user, g)
    } for g in groups])

@app.route('/api/update_channel_metadata', methods=['POST'])
@login_required
def update_channel_metadata():
    data = request.json
    channel_name = data.get('channel_name')
    new_display_name = data.get('display_name')
    new_icon_url = data.get('icon_url')
    
    ch = get_channel_in_context(current_user, channel_name=channel_name)
    if ch:
        ws = db.session.get(Workspace, ch.workspace_id)
        if is_public_ecosystem_workspace(ws):
            if not can_manage_public_group_post_policy(current_user, ws):
                return jsonify({'error': 'Unauthorized'}), 403
        elif not is_channel_manager(current_user):
            return jsonify({'error': 'Unauthorized'}), 403
        if new_display_name: ch.display_name = new_display_name
        if new_icon_url: ch.icon_url = new_icon_url
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'error': 'Channel not found'}), 404

@app.route('/api/update_channel_visibility', methods=['POST'])
@login_required
def update_channel_visibility():
    if not is_channel_manager(current_user):
        return jsonify({'error': 'Unauthorized'}), 403
        
    data = request.json
    channel_name = data.get('channel_name')
    visibility = data.get('visibility') # 'all' or 'se_tl'
    
    ch = get_channel_in_context(current_user, channel_name=channel_name)
    if ch:
        ch.visibility = visibility
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'error': 'Channel not found'}), 404

@app.route('/api/create_channel', methods=['POST'])
@login_required
def create_channel():
    # Defensive checks to catch any hidden formatting issues
    role = (current_user.role or "").lower().strip()
    t_role = (current_user.team_role or "").lower().strip()
    
    print(f">> Channel Creation Attempt: User={current_user.email}, Role={role}, TeamRole={t_role}")
    
    if not is_channel_manager(current_user):
        return jsonify({'error': 'Unauthorized', 'debug_role': role, 'debug_team_role': t_role}), 401

    data = request.json
    name = data.get('name')
    icon_url = data.get('icon_url')
    if not name: return jsonify({'error': 'No name'}), 400
    
    # Create a slug from the display name for internal use
    channel_slug = name.lower().replace(' ', '-')
    
    new_ch = Channel(
        name=channel_slug, 
        team_name=current_user.team_name, 
        display_name=name, 
        workspace_id=current_user.workspace_id,
        icon_url=icon_url,
        post_permission_mode='all_visible',
        is_private_group=False
    )
    db.session.add(new_ch)
    db.session.commit()
    
    # Auto-add creator as member
    db.session.add(GroupMember(group_id=new_ch.id, user_id=current_user.id))
    db.session.commit()
    
    return jsonify({'success': True, 'group_id': new_ch.id})

@app.route('/api/create_private_group', methods=['POST'])
@login_required
def create_private_group():
    # Prevent creation from public/private ecosystem workspaces.
    if current_user.workspace_id != 1:
        return jsonify({'error': 'Private groups can only be created in Team Ecosystem'}), 403

    role = (current_user.role or "").lower().strip()
    t_role = (current_user.team_role or "").lower().strip()
    if not is_channel_manager(current_user):
        return jsonify({'error': 'Unauthorized', 'debug_role': role, 'debug_team_role': t_role}), 401

    data = request.json
    name = data.get('name')
    icon_url = data.get('icon_url')
    if not name:
        return jsonify({'error': 'No name'}), 400

    group_slug = name.lower().replace(' ', '-')
    new_group = Channel(
        name=group_slug,
        team_name=current_user.team_name,
        display_name=name,
        workspace_id=current_user.workspace_id,
        icon_url=icon_url,
        visibility='all',
        post_permission_mode='custom',
        is_private_group=True
    )
    db.session.add(new_group)
    db.session.commit()

    # Creator is first member and can post.
    db.session.add(GroupMember(group_id=new_group.id, user_id=current_user.id))
    db.session.commit()

    return jsonify({'success': True, 'group_id': new_group.id})



@app.route('/api/leave_group', methods=['POST'])
@login_required
def leave_group():
    data = request.json
    group_id = data.get('group_id')
    silent = data.get('silent', False)

    group = get_channel_in_context(current_user, channel_id=group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404

    gm = GroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).first()
    if not gm: return jsonify({'error': 'Not a member'}), 404
    
    # Team Lead Check
    if current_user.team_role == 'teamlead':
        other_leads = GroupMember.query.join(User).filter(
            GroupMember.group_id == group_id,
            User.id != current_user.id,
            User.team_role == 'teamlead'
        ).count()
        if other_leads == 0:
            return jsonify({'error': 'Assign another Team Lead first.'}), 403

    group_name = gm.group.name
    db.session.delete(gm)
    
    if not silent:
        sys_msg = Message(
            sender_id=1, # System
            channel_name=group_name,
            content=f"{current_user.name or current_user.email.split('@')[0]} left the group",
            msg_type='system'
        )
        db.session.add(sys_msg)
        
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/disband_group', methods=['POST'])
@login_required
def disband_group():
    if not is_channel_manager(current_user):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json
    group_id = data.get('group_id')
    
    g = get_channel_in_context(current_user, channel_id=group_id)
    if not g: return jsonify({'error': 'Group not found'}), 404
    
    # Cascade delete
    GroupMember.query.filter_by(group_id=group_id).delete()
    ChannelRolePermission.query.filter_by(channel_id=group_id).delete()
    db.session.delete(g)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/get_group_details/<int:group_id>')
@login_required
def get_group_details(group_id):
    g = get_channel_in_context(current_user, channel_id=group_id)
    if not g or not can_user_view_channel(current_user, g):
        return jsonify({'error': 'Group not found'}), 404

    member_list = []
    source_labels = {
        'workspace': 'Visible in this group',
        'member': 'Added directly',
        'teamlead': 'Bulk: Team leads',
        'member_bulk': 'Bulk: Members',
        'admin': 'Admin access'
    }
    for record in get_channel_member_records(g, current_user):
        u = record['user']
        source = record['source']
        if source == 'member':
            source_label = source_labels['member']
        elif source == 'teamlead':
            source_label = source_labels['teamlead']
        elif source == 'member_bulk':
            source_label = source_labels['member_bulk']
        elif source == 'admin':
            source_label = source_labels['admin']
        else:
            source_label = source_labels['workspace']
        member_list.append({
            'id': u.id,
            'name': u.name or u.email.split('@')[0],
            'email': u.email,
            'profile_pic_url': u.profile_pic_url or f"https://ui-avatars.com/api/?name={u.name or u.email}&background=ffffff&color=111827",
            'team_role': u.team_role,
            'role': u.role,
            'designation': u.designation,
            'is_me': (u.id == current_user.id),
            'source': source,
            'source_label': source_label,
            'removable': record['removable']
        })
        
    return jsonify({
        'id': g.id,
        'name': g.name,
        'display_name': g.display_name or g.name,
        'icon_url': g.icon_url or f"https://ui-avatars.com/api/?name={g.name}&background=ffffff&color=111827",
        'visibility': g.visibility,
        'members': member_list,
        'can_post': can_user_post_to_channel(current_user, g),
        'can_manage': is_channel_manager(current_user),
        'post_permission_mode': g.post_permission_mode or 'all_visible',
        'bulk_roles': sorted(get_channel_bulk_roles(g))
    })

@app.route('/api/add_group_member', methods=['POST'])
@login_required
def add_group_member():
    data = request.json
    group_id = data.get('group_id')
    user_id = data.get('user_id') # Can be single ID or list for bulk
    
    g = get_channel_in_context(current_user, channel_id=group_id)
    if not g or not can_user_view_channel(current_user, g):
        return jsonify({'error': 'Unauthorized or group not found'}), 403

    ws = db.session.get(Workspace, g.workspace_id)
    eligible_ids = {u.id for u in get_ecosystem_members(ws)}

    uids = user_id if isinstance(user_id, list) else [user_id]
    added_names = []

    if current_user.role not in ['admin', 'superadmin'] and not GroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).first():
        db.session.add(GroupMember(group_id=group_id, user_id=current_user.id))

    for uid in uids:
        if uid not in eligible_ids:
            continue
        exists = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
        if not exists:
            db.session.add(GroupMember(group_id=group_id, user_id=uid))
            u = db.session.get(User, uid)
            if u:
                added_names.append(u.name or u.email.split('@')[0])
                # Show system message in group activity/chat
                sys_msg = Message(
                    sender_id=current_user.id, # Or a system user ID? User said "System message"
                    channel_name=g.name,
                    content=f"{u.name or u.email.split('@')[0]} has been added to the group",
                    msg_type='system',
                    timestamp=datetime.datetime.utcnow()
                )
                db.session.add(sys_msg)
    
    db.session.commit()
    return jsonify({'success': True, 'added': added_names})

@app.route('/api/remove_group_member', methods=['POST'])
@login_required
def remove_group_member():
    if not is_channel_manager(current_user):
        return jsonify({'error': 'Unauthorized'}), 403
        
    data = request.json
    group_id = data.get('group_id')
    user_id = data.get('user_id')

    g = get_channel_in_context(current_user, channel_id=group_id)
    if not g:
        return jsonify({'error': 'Group not found'}), 404

    GroupMember.query.filter_by(group_id=group_id, user_id=user_id).delete()
    db.session.commit()
    # No system message for removal
    return jsonify({'success': True})

@app.route('/api/get_channel_post_permissions')
@login_required
def get_channel_post_permissions():
    channel_id = request.args.get('channel_id', type=int)
    channel_name = request.args.get('channel_name')
    channel = get_channel_in_context(current_user, channel_id=channel_id, channel_name=channel_name)
    if not channel or not can_user_view_channel(current_user, channel):
        return jsonify({'error': 'Group not found'}), 404

    explicit_ids = sorted(get_channel_explicit_member_ids(channel))
    bulk_roles = sorted(get_channel_bulk_roles(channel))
    return jsonify({
        'mode': channel.post_permission_mode or 'all_visible',
        'user_ids': explicit_ids,
        'team_roles': bulk_roles
    })

@app.route('/api/update_channel_post_permissions', methods=['POST'])
@login_required
def update_channel_post_permissions():
    data = request.json
    channel_id = data.get('channel_id')
    channel_name = data.get('channel_name')
    mode = data.get('mode', 'all_visible')
    selected_roles = {role for role in data.get('team_roles', []) if role in ['teamlead', 'member']}

    channel = get_channel_in_context(current_user, channel_id=channel_id, channel_name=channel_name)
    if not channel:
        return jsonify({'error': 'Group not found'}), 404

    ws = db.session.get(Workspace, channel.workspace_id)
    if is_public_ecosystem_workspace(ws):
        if not can_manage_public_group_post_policy(current_user, ws):
            return jsonify({'error': 'Unauthorized'}), 403
    elif not is_channel_manager(current_user):
        return jsonify({'error': 'Unauthorized'}), 403

    if mode not in ['all_visible', 'custom', 'teamlead_only']:
        return jsonify({'error': 'Invalid permission mode'}), 400

    eligible_ids = {
        member.id for member in get_context_members(current_user, ws)
        if can_user_view_channel(member, channel)
    }

    selected_user_ids = {
        int(uid) for uid in data.get('user_ids', [])
        if str(uid).isdigit() and int(uid) in eligible_ids
    }

    if mode == 'custom' and current_user.role not in ['admin', 'superadmin']:
        selected_user_ids.add(current_user.id)

    channel.post_permission_mode = mode
    preserve_visibility_members = (not bool(getattr(channel, 'is_private_group', False))) and ((channel.visibility or '').strip().lower() == 'custom')
    if not preserve_visibility_members:
        GroupMember.query.filter_by(group_id=channel.id).delete()
    ChannelRolePermission.query.filter_by(channel_id=channel.id).delete()

    if mode == 'custom':
        for uid in sorted(selected_user_ids):
            db.session.add(GroupMember(group_id=channel.id, user_id=uid))

        if not is_public_ecosystem_workspace(ws):
            for team_role in sorted(selected_roles):
                db.session.add(ChannelRolePermission(channel_id=channel.id, team_role=team_role))

    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/search_ecosystem_members')
@login_required
def search_ecosystem_members():
    q = request.args.get('q', '').lower()
    ws = db.session.get(Workspace, current_user.workspace_id)
    users = get_manageable_members_for_teamlead_scope(current_user, ws, search_term=q, limit=20)
    
    return jsonify([{
        'id': u.id,
        'name': u.name or u.email.split('@')[0],
        'email': u.email,
        'profile_pic_url': u.profile_pic_url or f"https://ui-avatars.com/api/?name={u.name or u.email}&background=ffffff&color=111827"
    } for u in users])


@app.route('/api/get_ecosystem_members')
@login_required
def get_ecosystem_members_api():
    ws = db.session.get(Workspace, current_user.workspace_id)
    users = get_ecosystem_members(ws)
    return jsonify([{
        'id': u.id,
        'email': u.email,
        'name': u.name or u.email.split('@')[0],
        'profile_pic_url': u.profile_pic_url or f"https://ui-avatars.com/api/?name={u.name or u.email.split('@')[0]}&background=ffffff&color=111827",
        'role': u.role,
        'team_role': u.team_role,
        'designation': u.designation or 'SE',
        'is_me': u.id == current_user.id,
        'workspace_id': u.workspace_id,
    } for u in users])


@app.route('/api/get_workspace_members')
@login_required
def get_workspace_members_api():
    ws = db.session.get(Workspace, current_user.workspace_id)
    if ws and ws.id == 1 and current_user.team_name:
        users = get_context_members(current_user, ws)
    else:
        users = get_ecosystem_members(ws)
    return jsonify([{
        'id': u.id,
        'email': u.email,
        'name': u.name or u.email.split('@')[0],
        'profile_pic_url': u.profile_pic_url or f"https://ui-avatars.com/api/?name={u.name or u.email.split('@')[0]}&background=ffffff&color=111827",
        'role': u.role,
        'team_role': u.team_role,
        'designation': u.designation or 'SE',
        'is_me': u.id == current_user.id,
        'workspace_id': u.workspace_id,
    } for u in users])


@app.route('/api/update_group_visibility', methods=['POST'])
@login_required
def update_group_visibility():
    if (current_user.team_role or '').strip().lower() != 'teamlead' and current_user.role not in ['admin', 'superadmin']:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json or {}
    group_id = data.get('group_id')
    user_ids = data.get('user_ids', [])
    if not str(group_id).isdigit():
        return jsonify({'error': 'Invalid group id'}), 400

    g = get_channel_in_context(current_user, channel_id=int(group_id))
    if not g:
        return jsonify({'error': 'Group not found'}), 404

    ws = db.session.get(Workspace, g.workspace_id)
    eligible_ids = {u.id for u in get_manageable_members_for_teamlead_scope(current_user, ws)}
    selected_ids = {int(uid) for uid in user_ids if str(uid).isdigit() and int(uid) in eligible_ids}
    selected_ids.add(current_user.id)

    lead_ids = {u.id for u in get_ecosystem_members(ws) if (u.team_role or '').strip().lower() == 'teamlead' or u.role in ['admin', 'superadmin']}
    if not (selected_ids & lead_ids):
        return jsonify({'error': 'At least one Team Lead (or Admin) must remain in the group.'}), 400

    GroupMember.query.filter_by(group_id=g.id).delete(synchronize_session=False)
    db.session.add_all([GroupMember(group_id=g.id, user_id=uid) for uid in sorted(selected_ids)])
    if not bool(getattr(g, 'is_private_group', False)):
        g.visibility = 'custom'
    db.session.commit()
    return jsonify({'success': True, 'count': len(selected_ids)})


@app.route('/api/create_ecosystem', methods=['POST'])
@login_required
def create_ecosystem():
    if current_user.team_role != 'teamlead' and current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.json
    name = data.get('name')
    logo_url = data.get('logo_url')
    sync_members = data.get('sync_members', False)

    # Check if user is allowed to deploy publicly
    team_can_publish = False
    if current_user.team_name:
        user_team = Team.query.filter_by(name=current_user.team_name).first()
        if user_team:
            team_can_publish = user_team.can_deploy_publicly or False

    # Determine privacy: admin can always set it; teamlead only if team has publish rights
    requested_private = data.get('is_private', True)
    if current_user.role in ['admin', 'superadmin']:
        is_private = requested_private
    elif team_can_publish:
        is_private = requested_private  # Teamlead with permission can choose
    else:
        is_private = True  # Force private if no permission

    if not name: return jsonify({'error': 'Name is required'}), 400
        
    new_ws = Workspace(name=name, logo_url=logo_url, creator_id=current_user.id, is_private=is_private)
    db.session.add(new_ws)
    db.session.flush()
    
    # Create default team in new workspace
    new_team = Team(name=f"{name} Core", workspace_id=new_ws.id)
    db.session.add(new_team)
    db.session.flush()
    
    # Create default channel
    new_chan = Channel(name="general", display_name="General", team_name=new_team.name, workspace_id=new_ws.id)
    db.session.add(new_chan)

    # Always give creator access and move them to the team in that workspace context
    # Wait, the user's workspace_id is updated when switching.
    # So we don't change it here.
    access = WorkspaceAccess(user_id=current_user.id, workspace_id=new_ws.id)
    db.session.add(access)
    
    # We need a way to store the user's team in EACH workspace?
    # Current models.py only has one team_name per user.
    # This is a limitation. I'll stick to one team_name for now but it might cause issues on swap.
    # PROPER WAY: UserTeam table.
    
    if sync_members and current_user.team_name:
        # Get members of current team from ALL workspaces to ensure a full sync
        members = User.query.filter_by(team_name=current_user.team_name).all()
        for m in members:
            exists = WorkspaceAccess.query.filter_by(user_id=m.id, workspace_id=new_ws.id).first()
            if not exists:
                wa = WorkspaceAccess(user_id=m.id, workspace_id=new_ws.id)
                db.session.add(wa)
                # but when they switch workspace, they will need a team_name in that context.
                # For now, I'll assume they keep the same team_role? 
                # This is a bit complex for a quick fix.
                
    db.session.commit()
    return jsonify({'success': True, 'workspace_id': new_ws.id})

@app.route('/api/toggle_workspace_privacy', methods=['POST'])
@login_required
def toggle_workspace_privacy():
    data = request.json
    is_private = data.get('is_private', True)
    ws_id = data.get('workspace_id') or current_user.workspace_id

    ws = db.session.get(Workspace, ws_id)
    if not ws:
        return jsonify({'error': 'Workspace not found'}), 404
    if ws.id == 1:
        return jsonify({'error': 'Primary Hub visibility cannot be changed'}), 403

    # Allow: admin users OR the creator of this specific ecosystem
    is_creator = ws.creator_id == current_user.id
    is_admin = current_user.role in ['admin', 'superadmin']
    if not is_creator and not is_admin:
        return jsonify({'error': 'Only the ecosystem creator or an admin can change visibility.'}), 403

    ws.is_private = is_private
    db.session.commit()
    return jsonify({'success': True, 'is_private': ws.is_private})

@app.route('/api/get_public_ecosystems')
@login_required
def get_public_ecosystems():
    # IDs where user already has explicit ecosystem access
    accessible_ids = [wa.workspace_id for wa in WorkspaceAccess.query.filter_by(user_id=current_user.id).all()]
    # Also exclude user's own current primary workspace
    exclude_ids = list(set(accessible_ids + [current_user.workspace_id or 0, 1]))

    public_ws = Workspace.query.filter(
        Workspace.is_private == False,
        Workspace.id.notin_(exclude_ids)
    ).all()

    return jsonify([{
        'id': w.id,
        'name': w.name,
        'logo_url': w.logo_url or ('https://ui-avatars.com/api/?name=' + w.name + '&background=6366f1&color=fff')
    } for w in public_ws])

@app.route('/api/join_ecosystem', methods=['POST'])
@login_required
def join_ecosystem():
    data = request.json
    ws_id = data.get('workspace_id')
    ws = db.session.get(Workspace, ws_id)
    
    if not ws or ws.is_private:
        return jsonify({'error': 'Ecosystem not found or private'}), 404
        
    # Check if already has access
    exists = WorkspaceAccess.query.filter_by(user_id=current_user.id, workspace_id=ws_id).first()
    if not exists:
        db.session.add(WorkspaceAccess(user_id=current_user.id, workspace_id=ws_id))
        db.session.commit()
        
    return jsonify({'success': True})

@app.route('/api/share_ecosystem_with_team', methods=['POST'])
@login_required
def share_ecosystem_with_team():
    if current_user.team_role != 'teamlead' and current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
        
    data = request.json
    ws_id = data.get('workspace_id')
    
    # Check if user owns this ecosystem or is admin
    ws = db.session.get(Workspace, ws_id)
    if not ws or (ws.creator_id != current_user.id and current_user.role != 'admin'):
        return jsonify({'error': 'Unauthorized to share this ecosystem'}), 403
        
    if not current_user.team_name:
        return jsonify({'error': 'No team to share with'}), 400
        
    # Get all team members in the current system context
    members = User.query.filter_by(team_name=current_user.team_name).all()
    member_ids = {m.id for m in members}
    existing_ids = {
        wa.user_id for wa in WorkspaceAccess.query.filter(
            WorkspaceAccess.workspace_id == ws_id,
            WorkspaceAccess.user_id.in_(member_ids)
        ).all()
    }
    to_add = member_ids - existing_ids
    if to_add:
        db.session.add_all([WorkspaceAccess(user_id=uid, workspace_id=ws_id) for uid in to_add])
            
    db.session.commit()
    return jsonify({'success': True})
@app.route('/api/get_team_members_for_sharing')
@login_required
def get_team_members_for_sharing():
    ws_id = request.args.get('workspace_id', current_user.workspace_id, type=int)
    # If no team_name, show all members in primary workspace context for sharing
    if not current_user.team_name:
        members = User.query.filter_by(workspace_id=1).all()
    else:
        members = User.query.filter_by(team_name=current_user.team_name).all()
        
    access_ids = [wa.user_id for wa in WorkspaceAccess.query.filter_by(workspace_id=ws_id).all()]
    return jsonify([{'id':m.id,'email':m.email,'name':m.name or m.email.split('@')[0],'has_access':m.id in access_ids} for m in members])

@app.route('/api/update_ecosystem_access', methods=['POST'])
@login_required
def update_ecosystem_access():
    data = request.json
    ws_id, user_ids = data.get('workspace_id'), data.get('user_ids', [])
    ws = db.session.get(Workspace, ws_id)
    if not ws or (ws.creator_id != current_user.id and current_user.role not in ['admin', 'superadmin']):
        return jsonify({'error': 'Unauthorized'}), 403
        
    # Standardize user_ids to ints
    selected_uids = [int(u) for u in user_ids if str(u).isdigit()]
    
    # We only manage access for people in the user's team or everyone in Hub if no team
    if current_user.team_name:
        manageable_members = User.query.filter_by(team_name=current_user.team_name).all()
    else:
        manageable_members = User.query.all() # Fallback for admins with no team context
        
    manageable_ids = [m.id for m in manageable_members]
    
    # Remove access only for those we are actively managing (to preserve existing outside access)
    # But for a teamlead sharing with their team, we just sync the team status
    WorkspaceAccess.query.filter(WorkspaceAccess.workspace_id == ws_id, WorkspaceAccess.user_id.in_(manageable_ids)).delete(synchronize_session=False)
    
    # Re-add selected ones
    valid_selected_uids = [uid for uid in selected_uids if uid in manageable_ids]
    if valid_selected_uids:
        db.session.add_all([WorkspaceAccess(user_id=uid, workspace_id=ws_id) for uid in valid_selected_uids])
    
    # ENSURE the creator and current user ALWAYS have access
    existing_after_write = {
        wa.user_id for wa in WorkspaceAccess.query.filter_by(workspace_id=ws_id).all()
    }
    for must_have_id in list(set([ws.creator_id, current_user.id])):
        if must_have_id and must_have_id not in existing_after_write:
            db.session.add(WorkspaceAccess(user_id=must_have_id, workspace_id=ws_id))
            
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/update_ecosystem_identity', methods=['POST'])
@login_required
def update_ecosystem_identity():
    data = request.json
    ws_id = data.get('workspace_id')
    name = data.get('name')
    logo_url = data.get('logo_url')
    is_private = data.get('is_private')
    
    ws = db.session.get(Workspace, ws_id)
    if not ws or (ws.creator_id != current_user.id and current_user.role not in ['admin', 'superadmin']):
        return jsonify({'error': 'Unauthorized to edit this ecosystem'}), 403
        
    if name: ws.name = name
    if logo_url: ws.logo_url = logo_url
    if is_private is not None:
        # Prevent making Hub (ID 1) public
        if ws.id == 1 and not is_private:
            pass 
        else:
            ws.is_private = is_private
            
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/delete_ecosystem', methods=['POST'])
@login_required
def delete_ecosystem():
    data = request.json
    ws_id = data.get('workspace_id')
    ws = db.session.get(Workspace, ws_id)
    if not ws or (ws.creator_id != current_user.id and current_user.role not in ['admin', 'superadmin']):
        return jsonify({'error': 'Unauthorized to delete this ecosystem'}), 403
    
    if ws.id == 1: return jsonify({'error': 'Primary Hub cannot be deleted'}), 403
    
    # Cleanup associations if needed (cascades usually handle this if set up)
    db.session.delete(ws)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/get_ecosystems')
@login_required
def get_ecosystems():
    # Accessible via WorkspaceAccess (Private) OR All (Public)
    # But user said "initially make every ecosystem private"
    # and TL can toggle public
    
    # Workspaces where user has explicit access
    accessible_ids = [wa.workspace_id for wa in WorkspaceAccess.query.filter_by(user_id=current_user.id).all()]
    
    # Workspaces that are public
    public_workspaces = Workspace.query.filter_by(is_private=False).all()
    public_ids = [pw.id for pw in public_workspaces]

    # Workspaces the user CREATED (always visible to the creator for management)
    created_ids = [w.id for w in Workspace.query.filter_by(creator_id=current_user.id).all()]

    all_visible_ids = list(set(accessible_ids + public_ids + created_ids))

    # FILTER OUT ADMIN WORKSPACE (ID 1) FROM LIST
    if 1 in all_visible_ids:
        all_visible_ids.remove(1)

    workspaces = Workspace.query.filter(Workspace.id.in_(all_visible_ids)).all()

    return jsonify([{
        'id': w.id,
        'name': w.name,
        'logo_url': w.logo_url or f"https://ui-avatars.com/api/?name={w.name}&background=6366f1&color=fff",
        'is_private': w.is_private if w.is_private is not None else True,
        'is_creator': w.creator_id == current_user.id
    } for w in workspaces])

@app.route('/api/switch_workspace', methods=['POST'])
@login_required
def switch_workspace():
    data = request.json
    ws_id = data.get('workspace_id')

    # Check access
    has_access = WorkspaceAccess.query.filter_by(user_id=current_user.id, workspace_id=ws_id).first()
    ws = db.session.get(Workspace, ws_id)
    
    if ws and (not ws.is_private or has_access or current_user.role == 'superadmin'):
        current_user.workspace_id = ws_id
        # When switching workspace, we might need a default team?
        # For now, just switch the ID.
        db.session.commit()
        return jsonify({'success': True})
    
    return jsonify({'error': 'Access denied'}), 403


@app.route('/api/upload_voice', methods=['POST'])
@login_required
def upload_voice():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio part'}), 400
    file = request.files['audio']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    filename = str(uuid.uuid4()) + ".webm"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    
    return jsonify({
        'success': True,
        'file_path': f"/static/uploads/voice/{filename}"
    })

@app.route('/api/upload_image', methods=['POST'])
@login_required
def upload_image():
    if 'image' not in request.files:
        return jsonify({'error': 'No image part'}), 400
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    # Check extension
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
        return jsonify({'error': 'Invalid file type'}), 400

    img_folder = 'static/uploads/images'
    if not os.path.exists(img_folder):
        os.makedirs(img_folder, exist_ok=True)
        
    filename = str(uuid.uuid4()) + "." + ext
    filepath = os.path.join(img_folder, filename)
    file.save(filepath)
    
    return jsonify({
        'success': True,
        'file_path': f"/static/uploads/images/{filename}"
    })

@app.route('/api/upload_attachment', methods=['POST'])
@login_required
def upload_attachment():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    attachment_folder = 'static/uploads/attachments'
    if not os.path.exists(attachment_folder):
        os.makedirs(attachment_folder, exist_ok=True)

    original_name = os.path.basename(file.filename)
    name, ext = os.path.splitext(original_name)
    safe_name = ''.join(ch for ch in name if ch.isalnum() or ch in (' ', '-', '_')).strip() or 'attachment'
    safe_name = safe_name.replace(' ', '_')
    ext = (ext or '').lower()
    filename = f"{uuid.uuid4()}_{safe_name}{ext}"
    filepath = os.path.join(attachment_folder, filename)
    file.save(filepath)

    return jsonify({
        'success': True,
        'file_path': f"/static/uploads/attachments/{filename}",
        'file_name': original_name
    })

@app.route('/api/update_user_profile', methods=['POST'])
@login_required
def update_user_profile():
    data = request.json
    name = data.get('name')
    profile_pic_url = data.get('profile_pic_url')
    
    if name: current_user.name = name
    if profile_pic_url: current_user.profile_pic_url = profile_pic_url
    
    db.session.commit()
    return jsonify({'success': True})

if __name__ == '__main__':
    # Use socketio.run for real-time features
    socketio.run(app, host='0.0.0.0', port=5001, debug=True)
