from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


def _default_db_path() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    base = Path(configured) if configured and configured.strip() else Path.home() / ".local" / "share"
    return base / "InformationDietManager" / "idm.sqlite3"


def _configured_db_path() -> Path:
    configured = os.environ.get("IDM_DB_PATH")
    if configured is not None and not configured.strip():
        raise RuntimeError("IDM_DB_PATH must be a non-empty database file path.")
    path = (Path(configured) if configured is not None else _default_db_path()).expanduser().resolve()
    if path.is_dir():
        raise RuntimeError("Database path must name a file, not an existing directory.")
    return path


DB_PATH = _configured_db_path()


def init_db(schema_path: Path) -> None:
    # A missing/unreadable schema must not create an empty database file.
    schema = schema_path.read_text(encoding="utf-8")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        # executescript otherwise commits each DDL statement independently.
        # Keep additive setup atomic without attempting to migrate old tables.
        conn.executescript("BEGIN IMMEDIATE;\n" + schema + "\nCOMMIT;")
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass  # Preserve the initialization error if rollback also fails.
        raise
    finally:
        conn.close()


@contextmanager
def get_conn(*, timeout: float = 5.0) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
