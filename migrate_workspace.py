import sqlite3
import os

db_path = 'instance/chat.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Check current columns
    c.execute("PRAGMA table_info(workspace)")
    cols = [row[1] for row in c.fetchall()]
    print(f"Current columns: {cols}")
    
    if 'is_private' not in cols:
        print("Adding is_private column...")
        c.execute("ALTER TABLE workspace ADD COLUMN is_private BOOLEAN DEFAULT 1")
        
    if 'creator_id' not in cols:
        print("Adding creator_id column...")
        c.execute("ALTER TABLE workspace ADD COLUMN creator_id INTEGER REFERENCES user(id)")
        
    conn.commit()
    conn.close()
    print("Migration complete.")
else:
    print("Database not found.")
