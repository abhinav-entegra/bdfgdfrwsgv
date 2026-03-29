"""Generate unified_app.py from client_app.py — run from Copy folder."""
from pathlib import Path

src = Path("client_app.py").read_text(encoding="utf-8")
text = src.replace("client_app", "app")

text = text.replace(
    "from database_bootstrap import run_client_database_bootstrap\nfrom socket_auth import create_socket_token",
    "from database_bootstrap import run_client_database_bootstrap\nfrom admin_blueprint import admin_bp, create_initial_admin\nfrom socket_auth import create_socket_token",
)
text = text.replace(
    "from public_urls import (\n    get_admin_public_base_url,\n    get_signaling_public_base_url,\n    get_socketio_cors_origins,\n)",
    "from public_urls import (\n    get_signaling_public_base_url,\n    get_socketio_cors_origins,\n)",
)
text = text.replace(
    "app.config['SECRET_KEY'] = os.getenv('CLIENT_SECRET_KEY', 'clientsecretkey123')",
    "app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or os.getenv('CLIENT_SECRET_KEY') or os.getenv('ADMIN_SECRET_KEY') or 'dev-unified-secret'",
)
text = text.replace(
    "app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or os.getenv('CLIENT_SECRET_KEY') or os.getenv('ADMIN_SECRET_KEY') or 'dev-unified-secret'\nconfigure_sqlalchemy(app)",
    "app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or os.getenv('CLIENT_SECRET_KEY') or os.getenv('ADMIN_SECRET_KEY') or 'dev-unified-secret'\n\n"
    "# Standalone unified app: always SQLite next to this file (ignores DATABASE_URL).\n"
    "_unified_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), \"unified_chat.db\")\n"
    "os.environ.pop(\"DATABASE_URL\", None)\n"
    "configure_sqlalchemy(app)\n"
    "app.config[\"SQLALCHEMY_DATABASE_URI\"] = \"sqlite:///\" + _unified_db_path.replace(\"\\\\\", \"/\")",
)
text = text.replace(
    "app.config['SESSION_COOKIE_NAME'] = 'nexus_client_session'",
    "app.config['SESSION_COOKIE_NAME'] = 'nexus_session'",
)
text = text.replace("login_manager.login_view = 'login'", "login_manager.login_view = 'unified_login'")

text = text.replace(
    "with app.app_context():\n    run_client_database_bootstrap(app)\n\nlogin_manager = LoginManager(app)",
    "with app.app_context():\n    run_client_database_bootstrap(app)\n    create_initial_admin()\n\napp.register_blueprint(admin_bp)\n\nlogin_manager = LoginManager(app)",
)

old = """@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials.', 'error')
    return render_template('client_login.html', admin_portal_url=get_admin_public_base_url())

@app.route('/dashboard')
@login_required
def dashboard():"""

new = """@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('unified_login'))
    can_admin = current_user.role in ('admin', 'superadmin')
    return render_template('portal_hub.html', can_admin_console=can_admin)

@app.route('/login', methods=['GET', 'POST'])
def unified_login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        flash('Invalid credentials.', 'error')
    return render_template('unified_login.html')

@app.route('/dashboard')
@login_required
def dashboard():"""

if old not in text:
    raise SystemExit("Expected login block not found — client_app.py may have changed.")
text = text.replace(old, new)

text = text.replace(
    """@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))""",
    """@app.route('/logout')
@login_required
def unified_logout():
    logout_user()
    return redirect(url_for('unified_login'))""",
)

Path("unified_app.py").write_text(text, encoding="utf-8")
print("unified_app.py written")
