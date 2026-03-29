from app import app, db
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("ALTER TABLE channel ADD COLUMN visibility VARCHAR(50) DEFAULT 'all';"))
        db.session.commit()
        print("Migration successful: Added visibility column to channel.")
    except Exception as e:
        print(f"Migration skipped or failed: {e}")
