"""Portable page-record backups; credentials and derived analysis are excluded."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .db import get_conn
from .models import IngestItem

MAX_BACKUP_RECORDS = 10000
MAX_BACKUP_BYTES = 20 * 1024 * 1024
FIELDS = ("url", "title", "text", "ts", "source", "lang", "channel", "author", "tags", "meta")


class PageBackup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["idm-page-records"]
    version: Literal[1]
    exported_at: int = Field(strict=True, ge=0)
    items: list[IngestItem] = Field(max_length=MAX_BACKUP_RECORDS)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def canonical_items(items) -> bytes:
    return json.dumps(items, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def clear_derived(conn):
    for table in ("analysis_jobs", "analysis_runs", "stats_daily"):
        conn.execute(f"DELETE FROM {table}")  # Constants, never user input.


def clear_all(conn):
    clear_derived(conn)
    conn.execute("DELETE FROM embeddings")
    conn.execute("DELETE FROM items")


def require_confirmation(request: Request, value: str):
    if request.headers.get("X-IDM-Confirm") != value:
        raise HTTPException(400, "Explicit operation confirmation required")


def install_data_routes(app, insert_items):
    @app.get("/data/backup")
    def backup():
        items = []
        try:
            with get_conn() as conn:
                conn.execute("BEGIN")
                if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] > MAX_BACKUP_RECORDS:
                    raise HTTPException(413, "Backup supports at most 10000 page records")
                size = 2  # JSON array brackets; enforce the byte budget while reading rows.
                for row in conn.execute("SELECT * FROM items ORDER BY id"):
                    record = {key: row[key] for key in FIELDS}
                    for key in ("tags", "meta"):
                        record[key] = json.loads(record[key]) if record[key] is not None else None
                    record = IngestItem.model_validate(record).model_dump(mode="json")
                    size += len(canonical_items(record)) + int(bool(items))
                    if size > MAX_BACKUP_BYTES:
                        raise HTTPException(413, "Backup exceeds 20 MiB")
                    items.append(record)
            digest = hashlib.sha256(canonical_items(items)).hexdigest()
            payload = {"format": "idm-page-records", "version": 1, "exported_at": int(time.time() * 1000), "items": items, "sha256": digest}
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError, ValidationError, RecursionError):
            raise HTTPException(409, "Stored legacy records do not satisfy the current backup contract") from None
        if len(body) > MAX_BACKUP_BYTES:
            raise HTTPException(413, "Backup exceeds 20 MiB")
        return Response(body, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="idm-pages-backup.json"'})

    @app.delete("/items/{item_id}")
    def delete_item(item_id: int, request: Request):
        require_confirmation(request, "delete-record")
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not 1 <= item_id <= 2**63 - 1 or not conn.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone():
                raise HTTPException(404, "Record not found")
            conn.execute("DELETE FROM embeddings WHERE item_id = ?", (item_id,))
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            clear_derived(conn)
        return {"deleted": 1, "analysis_cleared": True}

    @app.delete("/data")
    def delete_all(request: Request):
        require_confirmation(request, "delete-all")
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            clear_all(conn)
        return {"deleted": count, "analysis_cleared": True}

    @app.post("/data/restore")
    async def restore(request: Request):
        require_confirmation(request, "replace-records")
        try:
            raw = await request.json()
            if not isinstance(raw, dict) or type(raw.get("version")) is not int:
                raise ValueError()
            backup = PageBackup.model_validate(raw)
            digest = hashlib.sha256(canonical_items(raw["items"])).hexdigest()
            if digest != backup.sha256:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise HTTPException(422, "Invalid, unsupported or damaged page-record backup") from None
        from starlette.concurrency import run_in_threadpool

        def replace():
            with get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                clear_all(conn)
                inserted, duplicates = insert_items(backup.items, connection=conn)
                if duplicates:
                    raise HTTPException(422, "Backup contains duplicate normalized page URLs; nothing changed")
            return {"restored": inserted, "analysis_cleared": True}

        return await run_in_threadpool(replace)
