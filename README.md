# nexusssss-finall

Nexus iQ unified app: Flask + Socket.IO, SQLite (`unified_chat.db`), single deploy (`unified_app.py` / root `Procfile`).

Run locally: `python unified_app.py`

On startup, Entegrasources gets **canonical teams** (Sales Alpha Core, Growth Ops Node, KPI Krushers, Deal Avengers, Ecosystem Core) if missing, and the primary superadmin is linked to the first team for the admin UI.

**Old data / legacy SQLite**

1. **Automatic (recommended):** Put your old database next to `unified_app.py` as **`chat.db`**, or set env **`LEGACY_SQLITE_FILE`** to its full path. On startup, rows are merged into **`unified_chat.db` before** seeding (so users/teams/messages come back). A marker in **`instance/legacy_sqlite_imported.txt`** prevents re-merging the same file; set **`FORCE_LEGACY_MERGE=1`** to merge again (e.g. after replacing `chat.db`).

2. **Manual** (app stopped): `python tools/merge_sqlite_legacy.py path/to/chat.db`

On Railway, the disk is ephemeral unless you use a **volume**—copy `chat.db` into the mounted path and set **`LEGACY_SQLITE_FILE`** to that path, or run the manual merge against a persistent volume.

# bdfgdfrwsgv
