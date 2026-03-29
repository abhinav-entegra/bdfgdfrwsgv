"""One-off script to generate admin_blueprint.py from app.py — run from Copy folder."""
import re

src = open("app.py", encoding="utf-8").read()
start = src.find("def create_initial_admin")
end = src.find("if __name__ == '__main__':")
body = src[start:end]

# Strip old login / logout on app
body = re.sub(
    r"@app\.route\('/', methods=\['GET', 'POST'\]\)\ndef login\(\):.*?(?=@app\.route\('/logout'\))",
    "",
    body,
    flags=re.DOTALL,
)
body = re.sub(
    r"@app\.route\('/logout'\)\n@login_required\ndef logout\(\):.*?(?=@app\.route\('/admin)",
    "",
    body,
    flags=re.DOTALL,
)

body = body.replace("@app.route", "@admin_bp.route")
body = body.replace("url_for('admin_dashboard')", "url_for('admin.admin_dashboard')")
body = body.replace("url_for('login')", "url_for('unified_login')")
body = body.replace("os.path.join(app.root_path", "os.path.join(current_app.root_path")

header = '''"""Admin routes (blueprint) for unified Nexus app."""
import os
import uuid
import datetime
import json
import urllib.request
import urllib.error

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy import text, or_
from werkzeug.security import generate_password_hash, check_password_hash

from models import db, User, Log, Message, Channel, Team, Workspace, WorkspaceAccess

admin_bp = Blueprint("admin", __name__)


'''

open("admin_blueprint.py", "w", encoding="utf-8").write(header + body)
print("admin_blueprint.py written")
