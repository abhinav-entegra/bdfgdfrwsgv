"""
One-time merge of an older SQLite file (e.g. chat.db) into the unified app database.

Usage (stop the app first to avoid locks):
  python tools/merge_sqlite_legacy.py path/to/chat.db

Targets unified_chat.db in the project root by default.
Uses INSERT OR IGNORE per row (unique constraints / PK collisions are skipped).

Requires: Python 3.9+ (stdlib sqlite3).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


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


def merge(source: Path, dest: Path) -> None:
    if not source.is_file():
        print(f"Source missing: {source}", file=sys.stderr)
        sys.exit(1)
    if not dest.is_file():
        print(f"Destination missing: {dest} (run the app once to create it)", file=sys.stderr)
        sys.exit(1)

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    dst.execute("PRAGMA foreign_keys = OFF")

    order = [
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
    total = 0
    for t in order:
        try:
            n = _copy_table(dst, src, t)
        except sqlite3.Error as e:
            print(f"[skip {t}] {e}", file=sys.stderr)
            continue
        if n:
            print(f"{t}: inserted ~{n} row(s)")
        total += n

    dst.commit()
    dst.close()
    src.close()
    print(f"Done. Check admin dashboard; if counts look wrong, restore a backup and adjust.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge legacy SQLite into unified_chat.db")
    ap.add_argument("legacy_db", type=Path, help="Path to old database (e.g. chat.db)")
    ap.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Destination DB (default: <project>/unified_chat.db)",
    )
    args = ap.parse_args()
    dest = args.dest or (_project_root() / "unified_chat.db")
    merge(args.legacy_db.resolve(), dest.resolve())


if __name__ == "__main__":
    main()
