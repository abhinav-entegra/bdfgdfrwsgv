import sqlite3
import os

paths = ['chat.db', 'instance/chat.db', 'instance/chat.sqlite']

for path in paths:
    if os.path.exists(path):
        print(f"Migrating {path}")
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        # Check columns
        cursor.execute("PRAGMA table_info(workspace)")
        columns = [c[1] for c in cursor.fetchall()]
        if 'allow_group_creation' not in columns:
            cursor.execute("ALTER TABLE workspace ADD COLUMN allow_group_creation BOOLEAN DEFAULT 1")
            print(f"Added allow_group_creation to workspace in {path}")
        else:
            print(f"allow_group_creation already exists in {path}")

        # Ensure group_member table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_member (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(group_id) REFERENCES channel(id),
                FOREIGN KEY(user_id) REFERENCES user(id)
            )
        """)
        print(f"Ensured group_member table exists in {path}")

        conn.commit()
        conn.close()
    else:
        print(f"Path {path} does not exist.")
