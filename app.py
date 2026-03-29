from dotenv import load_dotenv

load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
import os
import uuid
import datetime
import json
import urllib.request
import urllib.error

from models import db, User, Log, Message, Channel, Team, Workspace, WorkspaceAccess, migrate_encrypted_fields, ensure_performance_indexes
from db_config import configure_sqlalchemy, enable_sqlcipher_pragmas
from production_settings import apply_production_config
from public_urls import get_client_public_base_url, join_public_base

app = Flask(__name__)
apply_production_config(app)
app.config['SECRET_KEY'] = os.getenv('ADMIN_SECRET_KEY', 'supersecretchatkey123')
configure_sqlalchemy(app)
app.config['SESSION_COOKIE_NAME'] = 'nexus_admin_session'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 # 100MB LIMIT

db.init_app(app)
with app.app_context():
    enable_sqlcipher_pragmas(app, db)
    try:
        db.session.execute(text("ALTER TABLE channel ADD COLUMN post_permission_mode VARCHAR(50) DEFAULT 'all_visible'"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    db.create_all()
    ensure_performance_indexes()
    migrate_encrypted_fields()
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Initialize Admin User
def create_initial_admin():
    # Ensure Entegrasources workspace exists
    entegra_ws = Workspace.query.filter_by(name='Entegrasources').first()
    if not entegra_ws:
        entegra_ws = Workspace(name='Entegrasources', logo_url='/static/img/entegrasources_logo.png')
        db.session.add(entegra_ws)
        db.session.commit()

    admin_email = "abhinav.entegrasources@gmail.com"
    admin_user = User.query.filter_by(email=admin_email).first()
    if not admin_user:
        hashed_password = generate_password_hash('Hero@hero0012', method='pbkdf2:sha256')
        new_admin = User(email=admin_email, password=hashed_password, role='superadmin',
                         workspace_id=entegra_ws.id, name="Primary Superadmin")
        db.session.add(new_admin)
        db.session.commit()
        log_action("Primary Superadmin Node initialized.")
        admin_user = new_admin
    else:
        if admin_user.role != 'superadmin':
            admin_user.role = 'superadmin'
            db.session.commit()

    # Assign orphaned users/teams to Entegrasources
    User.query.filter_by(workspace_id=None).update({User.workspace_id: entegra_ws.id})
    Team.query.filter_by(workspace_id=None).update({Team.workspace_id: entegra_ws.id})
    db.session.commit()

    canonical = (
        "Sales Alpha Core",
        "Growth Ops Node",
        "KPI Krushers",
        "Deal Avengers",
        "Ecosystem Core",
    )
    added_or_linked = False
    for t_name in canonical:
        row = Team.query.filter_by(name=t_name).first()
        if row is None:
            db.session.add(Team(name=t_name, workspace_id=entegra_ws.id))
            added_or_linked = True
        elif row.workspace_id is None:
            row.workspace_id = entegra_ws.id
            added_or_linked = True
    db.session.commit()
    if added_or_linked:
        log_action("Primary ecosystem clusters synchronized (canonical teams ensured).")

    if admin_user and not (admin_user.team_name or "").strip():
        first_team = Team.query.filter_by(workspace_id=entegra_ws.id).order_by(Team.id.asc()).first()
        if first_team:
            admin_user.team_name = first_team.name
            db.session.commit()

def log_action(action):
    new_log = Log(action=action)
    db.session.add(new_log)
    db.session.commit()

def is_superadmin(user):
    return user.role == 'superadmin' # Formal Role check

def get_users_visible_in_workspace_admin(workspace_id):
    assigned = User.query.filter_by(workspace_id=workspace_id).all()
    merged = {u.id: u for u in assigned}
    access_ids = [wa.user_id for wa in WorkspaceAccess.query.filter_by(workspace_id=workspace_id).all()]
    ws = db.session.get(Workspace, workspace_id)
    if ws and ws.creator_id and ws.creator_id not in access_ids:
        access_ids.append(ws.creator_id)
    if access_ids:
        for u in User.query.filter(User.id.in_(access_ids)).all():
            merged[u.id] = u
    users = list(merged.values())
    users.sort(key=lambda u: ((u.name or u.email or '').lower(), (u.email or '').lower()))
    return users

def user_is_team_lead_for_admin(user):
    return (user.team_role or '').strip().lower() == 'teamlead'

# -- Authentication Routes --

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            if user.role in ['superadmin', 'admin']:
                remember = True if request.form.get('remember') else False
                login_user(user, remember=remember)
                log_action(f"User {user.email} logged in (Remember: {remember})")
                return redirect(url_for('admin_dashboard'))
            else:
                client_base = get_client_public_base_url()
                if client_base:
                    return redirect(join_public_base(client_base, '/'))
                flash('Access restricted to Administrators only.', 'error')
        else:
            flash('Invalid credentials, please try again.', 'error')
    
    return render_template('login.html', client_portal_url=get_client_public_base_url())

@app.route('/logout')
@login_required
def logout():
    log_action(f"User {current_user.email} logged out.")
    logout_user()
    return redirect(url_for('login'))

# -- Admin Routes --

@app.route('/admin/update_team_deployment/<int:team_id>', methods=['POST'])
@login_required
def update_team_deployment(team_id):
    if current_user.role != 'superadmin':
        flash('Access denied.', 'error')
        return redirect(url_for('admin_dashboard')) # Changed 'dashboard' to 'admin_dashboard' for consistency
    
    can_deploy = request.form.get('can_deploy_publicly') == '1'
    team = db.session.get(Team, team_id)
    if team:
        team.can_deploy_publicly = can_deploy
        db.session.commit()
        flash(f'Updated deployment rights for {team.name}', 'info')
        
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle_ecosystem_visibility/<int:ws_id>', methods=['POST'])
@login_required
def toggle_ecosystem_visibility(ws_id):
    if not is_superadmin(current_user):
        flash('Only Superadmins can change ecosystem visibility.', 'error')
        return redirect(url_for('admin_dashboard'))
    if ws_id == 1:
        flash('Primary Hub visibility cannot be changed.', 'error')
        return redirect(url_for('admin_dashboard'))
    ws = db.session.get(Workspace, ws_id)
    if not ws:
        flash('Workspace not found.', 'error')
        return redirect(url_for('admin_dashboard'))
    ws.is_private = not ws.is_private
    db.session.commit()
    status = 'PRIVATE' if ws.is_private else 'PUBLIC'
    log_action(f"Superadmin set workspace '{ws.name}' to {status}")
    flash(f"Ecosystem '{ws.name}' is now {status}.", 'info')
    return redirect(url_for('admin_dashboard') + '?section=ecosystems')

@app.route('/admin/toggle_group_creation/<int:ws_id>', methods=['POST'])
@login_required
def toggle_group_creation(ws_id):
    if not is_superadmin(current_user):
        flash('Only Superadmins can modify workspace-level permissions.', 'error')
        return redirect(url_for('admin_dashboard'))
    
    ws = db.session.get(Workspace, ws_id)
    if not ws:
        flash('Workspace not found.', 'error')
        return redirect(url_for('admin_dashboard'))
    
    ws.allow_group_creation = not ws.allow_group_creation
    db.session.commit()
    
    status = 'ENABLED' if ws.allow_group_creation else 'DISABLED'
    log_action(f"Superadmin set 'Allow Group Creation' to {status} for '{ws.name}'")
    flash(f"Group Creation for '{ws.name}' is now {status}.", 'info')
    return redirect(url_for('admin_dashboard') + '?section=ecosystems')

@app.route('/admin/api/update_workspace', methods=['POST'])
@login_required
def api_update_workspace():
    if current_user.role not in ['admin', 'superadmin']:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    ws = db.session.get(Workspace, current_user.workspace_id)
    if not ws:
        return jsonify({"success": False, "error": "Workspace not found"}), 404
        
    name = request.form.get('name')
    if name:
        ws.name = name
    
    theme_color = request.form.get('theme_color')
    if theme_color:
        ws.theme_color = theme_color
    
    logo_file = request.files.get('logo')
    if logo_file and logo_file.filename:
        # Create directory if missing
        upload_path = os.path.join(app.root_path, 'static', 'img', 'workspace_logos')
        if not os.path.exists(upload_path):
            os.makedirs(upload_path, exist_ok=True)
            
        # Unique filename
        ext = os.path.splitext(logo_file.filename)[1]
        new_filename = f"logo_{ws.id}_{uuid.uuid4().hex[:8]}{ext}"
        save_path = os.path.join(upload_path, new_filename)
        logo_file.save(save_path)
        
        # Update DB
        ws.logo_url = f"/static/img/workspace_logos/{new_filename}"
        
    db.session.commit()
    log_action(f"Workspace {ws.id} updated: Name: {name}, Logo: {ws.logo_url}")
    return jsonify({"success": True})

@app.route('/admin/dashboard', methods=['GET', 'POST'])
@login_required
def admin_dashboard():
    if current_user.role not in ['superadmin', 'admin']:
        flash('Access restricted to Privileged nodes.', 'error')
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        action_type = request.form.get('action_type', 'create_user')
        
        if action_type == 'create_user':
            new_email = request.form.get('new_email')
            new_password = request.form.get('new_password')
            role = request.form.get('role')
            team_name = request.form.get('team_name', None)
            team_role = 'member' if role in ['member', 'user'] else ('teamlead' if role == 'teamlead' else None)
            
            # Security: Standard admins cannot create Superadmins or regular Admins
            if not is_superadmin(current_user) and role in ['superadmin', 'admin']:
                flash('Insufficient privilege level for requested role assignment.', 'error')
            else:
                existing_user = User.query.filter_by(email=new_email).first()
                if existing_user:
                    flash('Account identity clash detected.', 'error')
                else:
                    hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
                    # Normalized roles for DB
                    db_role = 'admin' if role == 'admin' else ('superadmin' if role == 'superadmin' else 'user')
                    new_user = User(email=new_email, password=hashed_password, role=db_role, 
                                    team_name=team_name, team_role=team_role, designation='SE', 
                                    name=new_email.split('@')[0], workspace_id=current_user.workspace_id)
                    db.session.add(new_user)
                    db.session.commit()
                    log_action(f"Account {new_email} provisioned in {current_user.workspace.name}")
                    flash(f"Account {new_email} successfully decentralized.", 'success')
        
        elif action_type == 'create_team':
            team_name = request.form.get('team_name')
            if not team_name:
                flash('Team name cannot be empty', 'error')
            elif Team.query.filter_by(name=team_name, workspace_id=current_user.workspace_id).first():
                flash('Team already exists in this workspace', 'error')
            else:
                new_team = Team(name=team_name, workspace_id=current_user.workspace_id)
                db.session.add(new_team)
                db.session.commit()
                log_action(f"Admin created Team {team_name} in workspace {current_user.workspace.name}")
                flash(f"Team {team_name} created.", 'success')
            
    if is_superadmin(current_user):
        all_workspaces = Workspace.query.all()
        ws_id = request.args.get('ws_id', type=int)
        if ws_id:
            active_ws = Workspace.query.get(ws_id) or current_user.workspace
        else:
            active_ws = current_user.workspace
            
        users = get_users_visible_in_workspace_admin(active_ws.id)
        teams = Team.query.filter_by(workspace_id=active_ws.id).all()
        logs = Log.query.order_by(Log.timestamp.desc()).limit(100).all()
    else:
        active_ws = current_user.workspace
        all_workspaces = [active_ws]
        users = get_users_visible_in_workspace_admin(active_ws.id)
        teams = Team.query.filter_by(workspace_id=active_ws.id).all()
        logs = Log.query.filter(Log.action.contains(active_ws.name)).order_by(Log.timestamp.desc()).all()

    leaders = [u for u in users if u.role in ['admin', 'superadmin'] or user_is_team_lead_for_admin(u)]

    return render_template(
        'admin_dashboard.html',
        users=users,
        leaders=leaders,
        logs=logs,
        teams=teams,
        workspace=active_ws,
        all_workspaces=all_workspaces
    )

@app.route('/admin/personal/update_password', methods=['POST'])
@login_required
def personal_password_reset():
    if current_user.role not in ['superadmin', 'admin']:
        return {"error": "Unauthorized"}, 403
    new_password = request.form.get('new_password')
    if new_password:
        current_user.password = generate_password_hash(new_password, method='pbkdf2:sha256')
        db.session.commit()
        log_action(f"Privileged user {current_user.email} updated their own secret key.")
        flash("Personal security key updated.", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update_password/<int:user_id>', methods=['POST'])
@login_required
def update_password(user_id):
    if current_user.role not in ['superadmin', 'admin']:
        return redirect(url_for('login'))
    
    user = User.query.get_or_404(user_id)
    if current_user.role != 'superadmin' and user.workspace_id != current_user.workspace_id:
        return {"error": "Unauthorized"}, 403
    new_password = request.form.get('new_password')
    if new_password:
        user.password = generate_password_hash(new_password, method='pbkdf2:sha256')
        db.session.commit()
        log_action(f"Admin updated password for user: {user.email}")
        flash(f"Password updated successfully for {user.email}.", 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update_designation/<int:user_id>', methods=['POST'])
@login_required
def update_designation(user_id):
    if current_user.role not in ['superadmin', 'admin']:
        return redirect(url_for('login'))
    
    user = User.query.get_or_404(user_id)
    if current_user.role != 'superadmin' and user.workspace_id != current_user.workspace_id:
        return {"error": "Unauthorized"}, 403
    new_designation = request.form.get('designation')
    if new_designation in ['SE', 'SSE']:
        user.designation = new_designation
        db.session.commit()
        log_action(f"Admin updated designation for {user.email} to {new_designation}")
        flash(f"Designation updated for {user.email}.", 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/api/update_workspace', methods=['POST'])
@login_required
def update_workspace():
    if current_user.role not in ['superadmin', 'admin']:
        return {"error": "Unauthorized"}, 403
    data = request.json
    workspace = current_user.workspace
    if not workspace:
        return {"error": "No workspace assigned"}, 404
    
    workspace.name = data.get('name', workspace.name)
    workspace.logo_url = data.get('logo_url', workspace.logo_url)
    db.session.commit()
    
    log_action(f"Admin updated workspace configuration: {workspace.name}")
    return {"success": True, "name": workspace.name, "logo_url": workspace.logo_url}

@app.route('/admin/api/add_workspace', methods=['POST'])
@login_required
def add_workspace():
    if not is_superadmin(current_user):
        return {"error": "Only Superadmins (Entegrasources) can initiate deployment."}, 403
    
    data = request.json
    name = data.get('name')
    admin_email = data.get('admin_email')
    admin_password = data.get('admin_password')
    logo_url = data.get('logo_url', '/static/img/nexus-logo.png')
    
    if User.query.filter_by(email=admin_email).first():
        return {"error": "Administrator email collision detected."}, 400
        
    new_ws = Workspace(name=name, logo_url=logo_url)
    db.session.add(new_ws)
    db.session.flush()
    
    hashed_password = generate_password_hash(admin_password, method='pbkdf2:sha256')
    new_admin = User(email=admin_email, password=hashed_password, role='admin', 
                     workspace_id=new_ws.id, name=f"{name} Root Admin")
    db.session.add(new_admin)
    
    # Infrastructure Synchronization: Create default nodes
    default_team = Team(name="Ecosystem Core", workspace_id=new_ws.id)
    db.session.add(default_team)
    db.session.flush()
    
    default_chan = Channel(name="nexus-general", display_name="Nexus General", 
                           team_name=default_team.name, workspace_id=new_ws.id)
    db.session.add(default_chan)
    
    # Assign admin to the default team
    new_admin.team_name = default_team.name
    
    db.session.commit()
    log_action(f"Superadmin deployed new workspace: {name} (Admin: {admin_email})")
    return {"success": True, "id": new_ws.id, "name": new_ws.name}



@app.route('/admin/api/get_team_channels', methods=['POST'])
@login_required
def get_team_channels():
    if current_user.role != 'admin':
        return {"error": "Unauthorized"}, 403
    data = request.json
    teams = data.get('teams', [])
    if not teams:
        return {"channels": []}
    
    # Get all distinct channel names from messages of users in these teams
    channels = Message.query.join(User).filter(
        User.workspace_id == current_user.workspace_id,
        User.team_name.in_(teams)
    ).with_entities(Message.channel_name).distinct().all()
    channel_list = [c[0] for c in channels if c[0] and not c[0].startswith('DM:')] # Public channels
    has_dms = Message.query.join(User).filter(
        User.workspace_id == current_user.workspace_id,
        User.team_name.in_(teams),
        Message.channel_name.like('DM:%')
    ).first() is not None
    
    return {"channels": channel_list, "has_static_dms": has_dms}

@app.route('/admin/api/analyze_teams', methods=['POST'])
@login_required
def analyze_teams():
    if current_user.role != 'admin':
        return {"error": "Unauthorized"}, 403
        
    data = request.json
    api_key = data.get('api_key')
    teams = data.get('teams', [])
    prompt = data.get('prompt')
    selected_channels = data.get('channels', []) # List of strings: 'general', 'DMs', etc.
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    tz_offset = data.get('timezone_offset', 0)
    
    if not api_key:
        return {"error": "API Key is required"}, 400
    if not prompt:
        return {"error": "Prompt is required"}, 400
        
    # Fetch Messages
    query = Message.query.join(User, Message.sender_id == User.id)
    query = query.filter(User.workspace_id == current_user.workspace_id)
    if teams:
        query = query.filter(User.team_name.in_(teams))
        
    if selected_channels and 'all' not in selected_channels:
        # Complex logic: if 'DMs' is in list, include channel_name starting with DM:
        # plus any specifically named public channels
        public_chans = [c for c in selected_channels if c != 'DMs']
        filters = []
        if public_chans:
            filters.append(Message.channel_name.in_(public_chans))
        if 'DMs' in selected_channels:
            filters.append(Message.channel_name.like('DM:%'))
        
        if filters:
            from sqlalchemy import or_
            query = query.filter(or_(*filters))
        
    if start_date:
        query = query.filter(Message.timestamp >= datetime.datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        # Add 1 day to end_date to include the full final day
        query = query.filter(Message.timestamp <= datetime.datetime.strptime(end_date, '%Y-%m-%d') + datetime.timedelta(days=1))
        
    messages = query.order_by(Message.timestamp.asc()).all()
    
    if not messages:
        return {"result": "No communication data found for the selected parameters to analyze."}
        
    # Format chat log for prompt context
    chat_log = ""
    for msg in messages:
        # Apply localized timezone offset for analysis accuracy
        local_ts = msg.timestamp - datetime.timedelta(minutes=tz_offset)
        chat_log += f"[{local_ts.strftime('%Y-%m-%d %H:%M:%S')}] {msg.sender.email} ({msg.sender.team_name}): {msg.content}\n"
        
    # Build Gemini Request
    system_prompt = (
        "You are an expert team analyzer and internal auditor for Nexus iQ. "
        "You have been provided with the raw communication logs of specific teams. "
        "Analyze the data deeply and specifically answer the ADMIN's prompt. "
        "Format your response dynamically with HTML sections (e.g. <h4>, <ul>, <b>) for a dashboard view. Do not use markdown backticks.\n\n"
        "IMPORTANT: After your text analysis, you MUST include a JSON block for data visualization in this EXACT format:\n"
        "{\"chartData\": {\"labels\": [\"User1\", \"User2\"], \"values\": [10, 5], \"label\": \"Unnecessary Messages per User\"}}"
    )
    
    user_prompt = f"Admin Prompt: {prompt}\n\n--- CHAT LOGS ---\n{chat_log}"
    
    req_body = {
        "contents": [{"role": "user", "parts": [{"text": system_prompt + "\n\n" + user_prompt}]}],
        "generationConfig": {"temperature": 0.3}
    }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    req = urllib.request.Request(
        url,
        data=json.dumps(req_body).encode('utf-8'),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            resp_data = json.loads(response.read().decode('utf-8'))
            ai_text = resp_data['candidates'][0]['content']['parts'][0]['text']
            
            # Simple metrics counting
            lead_count = User.query.filter(
                User.workspace_id == current_user.workspace_id,
                User.team_name.in_(teams),
                User.team_role == 'teamlead'
            ).count() if teams else 0
            member_count = User.query.filter(
                User.workspace_id == current_user.workspace_id,
                User.team_name.in_(teams),
                User.team_role == 'member'
            ).count() if teams else 0
            
            return {
                "result": ai_text,
                "metrics": {
                    "leads": lead_count,
                    "members": member_count,
                    "total_messages": len(messages)
                }
            }
    except urllib.error.HTTPError as e:
        return {"error": f"Gemini API Error: {e.read().decode('utf-8')}"}, e.code
    except urllib.error.URLError as e:
        return {"error": f"Gemini Connection Error: {str(e.reason)}"}, 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_initial_admin()
    app.run(host='0.0.0.0', debug=True, port=5000)
