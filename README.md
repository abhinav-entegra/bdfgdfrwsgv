# nexusssss-finall

Nexus iQ unified app: Flask + Socket.IO, SQLite (`unified_chat.db`), single deploy (`unified_app.py` / root `Procfile`).

Run locally: `python unified_app.py`

On startup, Entegrasources gets **canonical teams** (Sales Alpha Core, Growth Ops Node, KPI Krushers, Deal Avengers, Ecosystem Core) if missing, and the primary superadmin is linked to the first team for the admin UI.

**Restore an older SQLite file** (e.g. old `chat.db`) into `unified_chat.db` (app stopped):

`python tools/merge_sqlite_legacy.py path/to/chat.db`

# bdfgdfrwsgv
