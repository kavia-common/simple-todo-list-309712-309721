#!/usr/bin/env python3
"""Initialize SQLite database for database.

This script is designed to be *re-runnable* (idempotent) and safe to execute multiple
times without errors. It will:

- Locate the SQLite DB file path:
  - Prefer the path found in db_connection.txt (if present and parseable)
  - Otherwise fall back to a local file named `myapp.db` in the current directory
- Create required tables if they do not exist, including the `tasks` table
  used by the todo application.

It also refreshes helper files:
- db_connection.txt (connection hints)
- db_visualizer/sqlite.env (for the Node.js DB viewer)
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DB_FALLBACK_NAME = "myapp.db"


# PUBLIC_INTERFACE
def resolve_sqlite_db_path() -> str:
    """Resolve the SQLite database file path.

    Resolution order:
    1) If db_connection.txt exists and contains a "File path:" line, use that path.
    2) Else if it contains a "Connection string:" line, attempt to parse sqlite:///... path.
    3) Else fall back to ./myapp.db (in current working directory).

    Returns:
        Absolute or relative path string suitable for sqlite3.connect().
    """
    # Prefer connection hints if present (per container guidance).
    hint_file = Path("db_connection.txt")
    if hint_file.exists():
        try:
            content = hint_file.read_text(encoding="utf-8", errors="ignore")
            # Example line:
            # # File path: /abs/path/to/myapp.db
            m = re.search(r"^\s*#\s*File path:\s*(.+?)\s*$", content, flags=re.MULTILINE)
            if m:
                return m.group(1).strip()

            # Example line:
            # # Connection string: sqlite:////abs/path/to/myapp.db
            m = re.search(
                r"^\s*#\s*Connection string:\s*(sqlite:(?P<slashes>/{2,4})(?P<path>.+?))\s*$",
                content,
                flags=re.MULTILINE,
            )
            if m:
                # For sqlite URIs, sqlite:///path (3 slashes) is common, sqlite:////abs (4 slashes)
                # yields an absolute path after the scheme.
                raw_path = m.group("path").strip()
                # If it looks like an absolute POSIX path already, keep it; else keep as-is.
                return raw_path
        except Exception:
            # If db_connection.txt is malformed, we fall back gracefully.
            pass

    return DB_FALLBACK_NAME


def _ensure_parent_dir(db_path: str) -> None:
    """Create parent directory for db file if it doesn't exist."""
    p = Path(db_path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)


def _utc_now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _create_schema(cursor: sqlite3.Cursor) -> None:
    """Create base schema + tasks table, idempotently."""
    # Existing sample tables kept for backward compatibility with the container template.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Required tasks table for the todo app.
    # Notes:
    # - completed is an INTEGER (0/1) for SQLite compatibility.
    # - created_at/updated_at are TEXT to store ISO8601 timestamps consistently.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '' NOT NULL,
            completed INTEGER DEFAULT 0 NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _seed_app_info(cursor: sqlite3.Cursor) -> None:
    """Upsert basic metadata into app_info (idempotent)."""
    cursor.execute(
        "INSERT OR REPLACE INTO app_info (key, value) VALUES (?, ?)",
        ("project_name", "database"),
    )
    cursor.execute(
        "INSERT OR REPLACE INTO app_info (key, value) VALUES (?, ?)",
        ("version", "0.1.0"),
    )
    cursor.execute(
        "INSERT OR REPLACE INTO app_info (key, value) VALUES (?, ?)",
        ("author", "John Doe"),
    )
    cursor.execute(
        "INSERT OR REPLACE INTO app_info (key, value) VALUES (?, ?)",
        ("description", ""),
    )


def _write_connection_hints(db_path: str) -> None:
    """Write db_connection.txt with helpful connection info."""
    abs_path = str(Path(db_path).resolve())
    connection_string = f"sqlite:////{abs_path.lstrip('/')}" if abs_path.startswith("/") else f"sqlite:///{abs_path}"
    try:
        with open("db_connection.txt", "w", encoding="utf-8") as f:
            f.write("# SQLite connection methods:\n")
            f.write(f"# Python: sqlite3.connect('{abs_path}')\n")
            f.write(f"# Connection string: {connection_string}\n")
            f.write(f"# File path: {abs_path}\n")
        print("Connection information saved to db_connection.txt")
    except Exception as e:
        print(f"Warning: Could not save connection info: {e}")


def _write_visualizer_env(db_path: str) -> None:
    """Write db_visualizer/sqlite.env for the Node DB viewer."""
    abs_path = str(Path(db_path).resolve())
    try:
        os.makedirs("db_visualizer", exist_ok=True)
        with open("db_visualizer/sqlite.env", "w", encoding="utf-8") as f:
            f.write(f'export SQLITE_DB="{abs_path}"\n')
        print("Environment variables saved to db_visualizer/sqlite.env")
    except Exception as e:
        print(f"Warning: Could not save environment variables: {e}")


def main() -> None:
    """Main entrypoint for database initialization."""
    db_path = resolve_sqlite_db_path()
    _ensure_parent_dir(db_path)

    print("Starting SQLite setup...")
    print(f"Target database file: {db_path}")

    db_exists = Path(db_path).exists()
    if db_exists:
        print(f"SQLite database already exists at {db_path}")
    else:
        print("Creating new SQLite database...")

    # Create/open database and ensure schema
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")

        _create_schema(cursor)
        _seed_app_info(cursor)

        # Optional: if this is a brand new DB, we still do not insert tasks here.
        # The app will manage tasks via the API. We only ensure schema exists.
        conn.commit()

        # Stats
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        table_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM app_info")
        record_count = cursor.fetchone()[0]

    finally:
        conn.close()

    # Refresh helper files (idempotent)
    _write_connection_hints(db_path)
    _write_visualizer_env(db_path)

    print("\nSQLite setup complete!")
    print(f"Database file: {db_path}")
    print("\nDatabase statistics:")
    print(f"  Tables: {table_count}")
    print(f"  App info records: {record_count}")
    print("\nTasks table schema created/verified:")
    print("  tasks(id INTEGER PK AUTOINCREMENT, title TEXT NOT NULL, description TEXT DEFAULT '' NOT NULL,")
    print("        completed INTEGER DEFAULT 0 NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    print("\nScript completed successfully.")


if __name__ == "__main__":
    main()
