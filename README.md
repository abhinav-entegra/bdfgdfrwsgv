# nexusssss-finall

Nexus iQ unified app: Flask + Socket.IO, SQLite (`unified_chat.db`), single deploy (`unified_app.py` / root `Procfile`).

Run locally: `python unified_app.py`

On startup, Entegrasources gets **canonical teams** (Sales Alpha Core, Growth Ops Node, KPI Krushers, Deal Avengers, Ecosystem Core) if missing, and the primary superadmin is linked to the first team for the admin UI.

### Data survives redeploy (Railway / Docker)

The deploy filesystem is **ephemeral**: **`unified_chat.db` in the repo root is deleted every redeploy** unless you change storage.

**Option A — Volume + SQLite:** Add a Railway **Volume** (e.g. mount **`/data`**). Set **`SQLITE_DATA_DIR=/data`**. The DB is **`/data/unified_chat.db`** and persists. Put **`chat.db`** in `/data` (or set **`LEGACY_SQLITE_FILE`**) to pull in old data once.

**Option B — Postgres:** Add Railway **Postgres**, link **`DATABASE_URL`**, set **`UNIFIED_USE_POSTGRES=true`**.

### Legacy SQLite import (SQLite mode only)

1. Auto: **`chat.db`** next to the app, under **`SQLITE_DATA_DIR`**, or **`LEGACY_SQLITE_FILE`**. Marker: **`legacy_sqlite_imported.txt`** next to the DB. **`FORCE_LEGACY_MERGE=1`** to merge again.  
2. Manual: `python tools/merge_sqlite_legacy.py path/to/chat.db`

# bdfgdfrwsgv
