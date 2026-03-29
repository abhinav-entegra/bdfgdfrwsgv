"""
Merge an older SQLite DB (e.g. chat.db) into unified_chat.db.

- Used automatically on startup when LEGACY_SQLITE_FILE or ./chat.db exists.
- CLI: python tools/merge_sqlite_legacy.py <path>
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

MARKER_NAME = "legacy_sqlite_imported.txt"

TABLE_ORDER = [
    "workspace",
    "team",
    "user",
    "workspace_access",
    "channel",
    "channel_role_permission",
    "group_member",
    "message",
    "notification",
    "channel_visit",
    "dm_permission",
    "log",
]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _copy_table(dst: sqlite3.Connection, src: sqlite3.Connection, table: str) -> int:
    if not src.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone():
        return 0
    try:
        d_cols = _table_columns(dst, table)
    except sqlite3.Error:
        return 0
    s_cols = _table_columns(src, table)
    cols = [c for c in d_cols if c in s_cols]
    if not cols:
        return 0
    placeholders = ",".join("?" * len(cols))
    col_list = ",".join(cols)
    sel = f"SELECT {col_list} FROM {table}"
    inserted = 0
    for row in src.execute(sel):
        try:
            cur = dst.execute(
                f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
                row,
            )
            if cur.rowcount and cur.rowcount > 0:
                inserted += cur.rowcount
        except sqlite3.IntegrityError:
            continue
    return inserted


def merge_legacy_sqlite_files(dest: Path, source: Path) -> dict[str, int]:
    """Copy rows from source into dest (FK checks off). Returns per-table insert counts."""
    dest = dest.resolve()
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Legacy DB not found: {source}")
    if not dest.is_file():
        raise FileNotFoundError(f"Destination DB not found: {dest}")
    if source == dest:
        raise ValueError("Source and destination must be different files")

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    dst.execute("PRAGMA foreign_keys = OFF")
    counts: dict[str, int] = {}
    for t in TABLE_ORDER:
        try:
            n = _copy_table(dst, src, t)
        except sqlite3.Error:
            n = 0
        if n:
            counts[t] = n
    dst.commit()
    dst.close()
    src.close()
    return counts


def _fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"


def try_auto_import_legacy(base_dir: str, dest_path: str) -> dict[str, int] | None:
    """
    If LEGACY_SQLITE_FILE is set or chat.db exists (app dir or same folder as unified_chat.db),
    merge into dest_path before seeding. Marker file lives next to dest (survives redeploy on a volume).
    """
    base = Path(base_dir)
    dest = Path(dest_path).resolve()
    if not dest.is_file():
        return None

    same_dir = dest.parent
    candidates: list[Path] = []
    env_path = os.getenv("LEGACY_SQLITE_FILE", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(same_dir / "chat.db")
    candidates.append(base / "chat.db")

    source: Path | None = None
    for c in candidates:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r.is_file() and r != dest:
            source = r
            break
    if not source:
        return None

    marker_path = same_dir / MARKER_NAME
    force = os.getenv("FORCE_LEGACY_MERGE", "").lower() in ("1", "true", "yes")
    fp = _fingerprint(source)

    if marker_path.is_file() and not force:
        try:
            if marker_path.read_text(encoding="utf-8").strip() == fp:
                return None
        except OSError:
            pass

    counts = merge_legacy_sqlite_files(dest, source)
    try:
        marker_path.write_text(fp, encoding="utf-8")
    except OSError:
        pass
    total = sum(counts.values())
    print(
        f"[legacy_sqlite_import] Merged {source.name} -> {dest.name} ({total} new rows across {len(counts)} tables)",
        flush=True,
    )
    return counts
