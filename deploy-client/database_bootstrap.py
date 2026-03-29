"""Shared schema setup for client-facing apps (main client + optional signaling service)."""
from sqlalchemy import text

from models import (
    Channel,
    User,
    Workspace,
    WorkspaceAccess,
    db,
    ensure_performance_indexes,
    migrate_encrypted_fields,
)


def run_client_database_bootstrap(app) -> None:
    """Run inside app.app_context() after db.init_app."""
    from db_config import enable_sqlcipher_pragmas

    enable_sqlcipher_pragmas(app, db)
    try:
        db.session.execute(text("ALTER TABLE user ADD COLUMN is_restricted BOOLEAN DEFAULT 0"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    try:
        db.session.execute(text("ALTER TABLE user ADD COLUMN dm_allowlist_only BOOLEAN DEFAULT 0"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE workspace ADD COLUMN allow_group_creation BOOLEAN DEFAULT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(
            text("ALTER TABLE channel ADD COLUMN post_permission_mode VARCHAR(50) DEFAULT 'all_visible'")
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
    try:
        db.session.execute(text("ALTER TABLE channel ADD COLUMN is_private_group BOOLEAN DEFAULT 0"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    db.create_all()
    ensure_performance_indexes()
    migrate_encrypted_fields()

    users_without_names = User.query.filter((User.name == None) | (User.name == "")).all()
    for u in users_without_names:
        u.name = u.email.split("@")[0]

    Channel.query.filter(
        (Channel.post_permission_mode == None) | (Channel.post_permission_mode == "")
    ).update({Channel.post_permission_mode: "all_visible"}, synchronize_session=False)

    all_users = User.query.all()
    for u in all_users:
        if u.workspace_id:
            exists = WorkspaceAccess.query.filter_by(user_id=u.id, workspace_id=u.workspace_id).first()
            if not exists:
                db.session.add(WorkspaceAccess(user_id=u.id, workspace_id=u.workspace_id))

    entegra = db.session.get(Workspace, 1)
    if entegra:
        entegra.is_private = False

    db.session.commit()
