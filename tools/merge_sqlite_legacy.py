"""
Merge an older SQLite file into unified_chat.db (manual one-off).

Usage (stop the app first to avoid locks):
  python tools/merge_sqlite_legacy.py path/to/chat.db

Implementation lives in legacy_sqlite_import.py (also used automatically on startup).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from legacy_sqlite_import import merge_legacy_sqlite_files  # noqa: E402


def _project_root() -> Path:
    return ROOT


def merge(source: Path, dest: Path) -> None:
    counts = merge_legacy_sqlite_files(Path(dest).resolve(), Path(source).resolve())
    total = sum(counts.values())
    for t, n in counts.items():
        print(f"{t}: +{n} row(s)")
    print(f"Done. {total} rows inserted (INSERT OR IGNORE).")


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
