from __future__ import annotations

import csv
import asyncio
import hashlib
import io
import json
import math
import re
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .db import get_conn, init_db
from .models import IngestAck, IngestItem, MAX_INGEST_TS
from .utils import normalize_text, normalize_url, sha256_hex
from . import db
from .security import LocalAccessMiddleware, LocalSecurity, process_ownership
from .data_management import install_data_routes
from .item_pagination import read_cursor_page
from .visualization_metrics import finite_metric

from contextlib import asynccontextmanager, nullcontext

from urllib.parse import urlparse

MAX_TEXT_LEN = 1000
EMBEDDING_DIM = 128
EMBEDDING_MODEL = "hash-chargram-v1"
_WORD_RE = re.compile(r"\w+", re.UNICODE)
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"
DEFAULT_VIS_DAYS = 7
MIN_VIS_RECORDS = 5
DEFAULT_REPEAT_THRESHOLD = 0.85
CATEGORY_ALIAS_MAP = {
    "entertainment": "ent",
    "learning": "edu",
    "news": "news",
    "social": "soc",
    "shopping": "shopping",
    "tools": "tools",
    "other": "other",
}
CHANNEL_CANONICAL_KEYS = ("ent", "edu", "news", "soc", "other")
CHANNEL_ALIAS_MAP = {
    "ent": "ent",
    "edu": "edu",
    "news": "news",
    "soc": "soc",
    "other": "other",
    "entertainment": "ent",
    "learning": "edu",
    "social": "soc",
    "娱乐": "ent",
    "学习": "edu",
    "新闻": "news",
    "社交": "soc",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _schema_path() -> Path:
    return Path(__file__).resolve().parent / "schema.sql"


def _as_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _parse_tags(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        parts = [p.strip() for p in stripped.split("|") if p.strip()]
        return parts or None
    return None


def _parse_meta(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _coerce_int(value: Any, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(f"Invalid {field}: {value!r}")


def _clean_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _truncate_text(value: Optional[str], max_len: int = MAX_TEXT_LEN) -> Optional[str]:
    if value is None:
        return None
    return value[:max_len] if len(value) > max_len else value


def _prepare_item(raw: Dict[str, Any]) -> IngestItem:
    data = {str(k).strip(): v for k, v in raw.items() if k is not None}
    for key in ("url", "title", "text", "source", "lang", "channel", "author"):
        if key in data:
            data[key] = _clean_optional_str(data.get(key))
    if "tags" in data:
        data["tags"] = _parse_tags(data.get("tags"))
    if "meta" in data:
        data["meta"] = _parse_meta(data.get("meta"))
    title = data.get("title")
    text = data.get("text")
    if text is None and title:
        text = title
    data["text"] = _truncate_text(text)
    if "ts" in data:
        data["ts"] = _coerce_int(data.get("ts"), "ts")
    return IngestItem(**data)


def _row_from_item(item: IngestItem) -> Dict[str, Any]:
    title = (item.title or "").strip()
    text = _clean_optional_str(item.text)
    if text is None:
        text = title
    text = _truncate_text(text)
    url_norm = normalize_url(str(item.url))
    content_norm = normalize_text(title, text)
    return {
        "url": str(item.url),
        "title": title,
        "text": text,
        "ts": item.ts,
        "source": item.source,
        "lang": item.lang,
        "channel": item.channel,
        "author": item.author,
        "tags": _as_json(item.tags),
        "meta": _as_json(item.meta),
        "url_hash": sha256_hex(url_norm),
        "content_hash": sha256_hex(content_norm),
        "created_at": _now_ms(),
    }


def _embedding_input_text(title: Optional[str], text: Optional[str]) -> str:
    base = _clean_optional_str(text) or _clean_optional_str(title) or ""
    normalized = normalize_text("", base)
    return _truncate_text(normalized, max_len=MAX_TEXT_LEN) or ""


def _vector_to_blob(vector: List[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _hashed_chargram_embedding(text: str, dim: int = EMBEDDING_DIM) -> List[float]:
    vec = [0.0] * dim
    tokens = _WORD_RE.findall(text)
    if not tokens:
        return vec
    for tok in tokens:
        grams: List[str]
        if len(tok) < 3:
            grams = [tok]
        else:
            grams = [tok[i : i + 3] for i in range(len(tok) - 2)]
        for gram in grams:
            h = int.from_bytes(
                hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(),
                "little",
            )
            idx = h % dim
            sign = 1.0 if ((h >> 1) & 1) == 0 else -1.0
            vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _upsert_embedding(
    conn: Any, item_id: int, title: Optional[str], text: Optional[str]
) -> None:
    input_text = _embedding_input_text(title, text)
    vector = _hashed_chargram_embedding(input_text, dim=EMBEDDING_DIM)
    conn.execute(
        """
        INSERT INTO embeddings (item_id, vector, vector_path, model, dim, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            vector=excluded.vector,
            vector_path=excluded.vector_path,
            model=excluded.model,
            dim=excluded.dim,
            created_at=excluded.created_at
        """,
        (
            item_id,
            _vector_to_blob(vector),
            None,
            EMBEDDING_MODEL,
            EMBEDDING_DIM,
            _now_ms(),
        ),
    )


def _backfill_missing_embeddings(conn: Any, limit: int = 2000) -> int:
    rows = conn.execute(
        """
        SELECT i.id, i.title, i.text
        FROM items AS i
        LEFT JOIN embeddings AS e ON e.item_id = i.id
        WHERE e.item_id IS NULL
        ORDER BY i.id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    count = 0
    for row in rows:
        _upsert_embedding(conn, int(row["id"]), row["title"], row["text"])
        count += 1
    return count


def _item_dataset_state(conn: Any) -> Dict[str, Any]:
    """Read inside the caller's transaction; timestamps alone miss same-ms inserts."""
    state = dict(conn.execute(
        "SELECT COUNT(*) AS total_count, COALESCE(MAX(id), 0) AS max_id, "
        "COALESCE(MAX(created_at), 0) AS max_created_at FROM items"
    ).fetchone())
    state.update(dict(conn.execute(
        "SELECT database_id, revision FROM items_revision WHERE singleton = 1"
    ).fetchone()))
    return state


def _stable_hash_payload(data: Dict[str, Any]) -> str:
    payload = json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return sha256_hex(payload)


def _lsj_algorithms_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "lsj" / "src" / "algorithms"


def _ensure_lsj_import_path() -> None:
    alg_dir = _lsj_algorithms_dir()
    path = str(alg_dir)
    if path not in sys.path:
        sys.path.insert(0, path)


def _normalize_category_key(value: Any) -> str:
    return str(value or "other").strip().lower() or "other"


def _canonicalize_channel_key(value: Any) -> str:
    key = _clean_optional_str(value) or "other"
    return CHANNEL_ALIAS_MAP.get(key.lower(), "other")


def _normalize_channel_counts(counts: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    if counts is None:
        return None

    normalized: Dict[str, int] = {}
    for raw_key, raw_count in counts.items():
        canonical_key = _canonicalize_channel_key(raw_key)
        try:
            count = int(raw_count or 0)
        except (TypeError, ValueError):
            continue
        normalized[canonical_key] = normalized.get(canonical_key, 0) + count

    return {key: normalized.get(key, 0) for key in CHANNEL_CANONICAL_KEYS}


def _default_visualization_payload() -> Dict[str, Any]:
    return {
        "time_series": [],
        "category_distribution": {},
        "sentiment_distribution": {},
        "similarity_histogram": {},
        "hourly_distribution": {},
    }


def _default_visualization_result(
    *,
    from_ts: Optional[int],
    to_ts: Optional[int],
    limit_rows: int,
    input_count: int = 0,
    generated_at: Optional[int] = None,
    pipeline_warning: Optional[str] = None,
) -> Dict[str, Any]:
    aliases = {
        key: alias for key, alias in sorted(CATEGORY_ALIAS_MAP.items(), key=lambda item: item[0])
    }
    return {
        "window": {
            "from_ts": from_ts,
            "to_ts": to_ts,
            "limit_rows": limit_rows,
            "input_count": input_count,
        },
        "global": _default_visualization_payload(),
        "categories": {},
        "category_aliases": aliases,
        "category_counts": {},
        "analysis_status": "not_computed",
        "minimum_records": MIN_VIS_RECORDS,
        "date_timezone": "UTC",
        "pipeline_warning": pipeline_warning,
        "generated_at": generated_at if generated_at is not None else _now_ms(),
    }


def _prepare_analysis_dataframe(rows: List[Dict[str, Any]]) -> Any:
    import pandas as pd  # type: ignore

    df = pd.DataFrame(rows)
    df["title"] = df["title"].fillna("").astype(str)
    df["text"] = df["text"].fillna(df["title"]).astype(str)
    df["analysis_text"] = df["text"].where(
        df["text"].str.strip() != "", df["title"]
    )
    df["url"] = df["url"].fillna("").astype(str)
    df["channel"] = df["channel"].fillna("unknown").astype(str)
    return df


def _execute_lsj_pipeline(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"ok": True, "input_count": 0}

    try:
        _ensure_lsj_import_path()
        from classifier import ContentClassifier  # type: ignore
        from evaluator import InformationQualityEvaluator  # type: ignore
        from sentiment import SentimentAnalyzer  # type: ignore
        from similarity import SimilarityAnalyzer  # type: ignore

        df = _prepare_analysis_dataframe(rows)

        classifier = ContentClassifier()
        sentiment = SentimentAnalyzer()
        similarity = SimilarityAnalyzer()
        evaluator = InformationQualityEvaluator(
            sentiment_analyzer=sentiment,
            content_classifier=classifier,
            similarity_analyzer=similarity,
        )

        df1 = classifier.batch_predict(
            df[["id", "title", "url", "analysis_text", "channel", "ts"]].copy()
        )
        df2 = sentiment.batch_predict(
            df1, text_column="analysis_text", include_emotions=False, batch_size=500
        )
        df3 = similarity.batch_calculate_similarity(df2, text_column="analysis_text")
        if "similarity" not in df3.columns and "similarity_to_previous" in df3.columns:
            df3["similarity"] = df3["similarity_to_previous"]

        return {
            "ok": True,
            "input_count": int(len(df3)),
            "evaluator": evaluator,
            "df3": df3,
        }
    except Exception:
        # Model exceptions can contain source text or private filesystem paths.
        # Do not expose/persist them or replace failed inference with hash scores.
        return {
            "ok": False,
            "input_count": len(rows),
            "warning": "Experimental analysis is unavailable; check the local analysis dependencies and configuration.",
        }


def _build_visualization_result(
    rows: List[Dict[str, Any]],
    *,
    from_ts: Optional[int],
    to_ts: Optional[int],
    limit_rows: int,
) -> Dict[str, Any]:
    base = _default_visualization_result(
        from_ts=from_ts,
        to_ts=to_ts,
        limit_rows=limit_rows,
        input_count=len(rows),
    )
    if not rows:
        base["analysis_status"] = "empty"
        return base
    if len(rows) < MIN_VIS_RECORDS:
        base["analysis_status"] = "insufficient_data"
        return base
    execution = _execute_lsj_pipeline(rows)

    if not execution.get("ok"):
        base["analysis_status"] = "unavailable"
        base["pipeline_warning"] = execution.get("warning")
        return base

    try:
        from .visualization_metrics import build_visualization_metrics

        df3 = execution["df3"]
        # Adjacent-pair results only describe the original (ts, id) sequence.
        # A pipeline that drops or reorders rows must not silently relabel them.
        if (len(df3) != len(rows) or df3["id"].tolist() != [r["id"] for r in rows]
                or df3["ts"].tolist() != [r["ts"] for r in rows]):
            raise ValueError("Pipeline record alignment changed")
        metrics = build_visualization_metrics(
            df3, repeat_threshold=DEFAULT_REPEAT_THRESHOLD, category_aliases=CATEGORY_ALIAS_MAP,
        )
    except Exception:
        # Do not turn a failed metric computation into zeroes or a successful chart.
        base["analysis_status"] = "failed"
        base["pipeline_warning"] = "visualization computation failed"
        return base

    base["window"]["processed_count"] = metrics["processed_count"]
    base["analysis_status"] = "ready"
    base["global"] = metrics["global"]
    base["categories"] = metrics["categories"]
    base["category_counts"] = metrics["category_counts"]
    base["coverage"] = metrics["coverage"]
    return base


def _load_items_for_analysis(
    conn: Any,
    from_ts: Optional[int],
    to_ts: Optional[int],
    limit_rows: int,
) -> List[Dict[str, Any]]:
    where_parts = ["1=1"]
    params: List[Any] = []
    if from_ts is not None:
        where_parts.append("ts >= ?")
        params.append(from_ts)
    if to_ts is not None:
        where_parts.append("ts <= ?")
        params.append(to_ts)
    where_sql = " AND ".join(where_parts)
    sql = f"""
        SELECT id, url, title, text, ts, channel, created_at
        FROM items
        WHERE {where_sql}
        ORDER BY ts ASC, id ASC
        LIMIT ?
    """
    params.append(limit_rows)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def _insert_analysis_job(
    conn: Any,
    *,
    status: str,
    input_hash: str,
    day: str,
    from_ts: Optional[int],
    to_ts: Optional[int],
    limit_rows: int,
    item_max_created_at: int,
    input_count: int,
    cache_hit: bool,
) -> int:
    now_ms = _now_ms()
    cur = conn.execute(
        """
        INSERT INTO analysis_jobs (
            status, input_hash, day, from_ts, to_ts, limit_rows, item_max_created_at,
            input_count, cache_hit, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            status,
            input_hash,
            day,
            from_ts,
            to_ts,
            limit_rows,
            item_max_created_at,
            input_count,
            1 if cache_hit else 0,
            now_ms,
            now_ms,
        ),
    )
    return int(cur.lastrowid)


def _update_analysis_job(
    conn: Any,
    job_id: int,
    *,
    status: str,
    error: Optional[str] = None,
    result_payload: Optional[Dict[str, Any]] = None,
    metrics_json: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[int] = None,
    started_at: Optional[int] = None,
    finished_at: Optional[int] = None,
) -> None:
    now_ms = _now_ms()
    conn.execute(
        """
        UPDATE analysis_jobs
        SET status = ?,
            error = ?,
            result_payload = COALESCE(?, result_payload),
            metrics_json = COALESCE(?, metrics_json),
            duration_ms = COALESCE(?, duration_ms),
            started_at = COALESCE(?, started_at),
            finished_at = COALESCE(?, finished_at),
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            error,
            _as_json(result_payload) if result_payload is not None else None,
            _as_json(metrics_json) if metrics_json is not None else None,
            duration_ms,
            started_at,
            finished_at,
            now_ms,
            job_id,
        ),
    )


class AnalysisUnavailableError(RuntimeError):
    """Expected optional-inference failure; never carries raw model exceptions."""


def _run_lsj_pipeline(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {
            "input_count": 0,
            "category_counts": {},
            "sentiment_counts": {},
            "comparison_count": 0,
            "sentiment_count": 0,
            "polarity_count": 0,
            "negative_ratio": None,
            "avg_sentiment": None,
            "repeat_ratio": None,
            "quick_evaluation": None,
            "full_report": None,
        }

    execution = _execute_lsj_pipeline(rows)
    if not execution.get("ok"):
        raise AnalysisUnavailableError("Experimental analysis is unavailable.")

    evaluator = execution["evaluator"]
    df3 = execution["df3"]
    quick = evaluator.quick_evaluate(df3)
    report = evaluator.evaluate(df3, detailed=False).to_dict()

    category_norm = df3["category"].fillna("other").astype(str)
    similarities: List[float] = []
    polarities: List[float] = []
    sentiment_counts: Dict[str, int] = {}
    for index, row in enumerate(df3.to_dict("records")):
        similarity = finite_metric(row.get("similarity"), 0, 1)
        if (index > 0 and similarity is not None
                and ("similarity_valid" not in row or row["similarity_valid"] is True)):
            similarities.append(similarity)
        sentiment = row.get("sentiment")
        label = sentiment.strip().lower() if isinstance(sentiment, str) else None
        if (label not in {"positive", "neutral", "negative"}
                or ("sentiment_valid" in row and row["sentiment_valid"] is not True)):
            continue
        # Only the metric with missing measurements loses observations; category
        # counts above retain the complete input, including the first record.
        key = label.title()
        sentiment_counts[key] = sentiment_counts.get(key, 0) + 1
        polarity = finite_metric(row.get("polarity"), -1, 1)
        if polarity is not None:
            polarities.append(polarity)
    sentiment_count = sum(sentiment_counts.values())
    negative_ratio = sentiment_counts.get("Negative", 0) / sentiment_count if sentiment_count else None
    avg_sentiment = math.fsum(polarities) / len(polarities) if polarities else None
    repeat_ratio = (sum(value >= DEFAULT_REPEAT_THRESHOLD for value in similarities) / len(similarities)
                    if similarities else None)

    category_counts = {
        str(k): int(v) for k, v in category_norm.value_counts().to_dict().items()
    }
    return {
        "input_count": int(execution["input_count"]),
        "category_counts": category_counts,
        "sentiment_counts": sentiment_counts,
        "comparison_count": len(similarities),
        "sentiment_count": sentiment_count,
        "polarity_count": len(polarities),
        "negative_ratio": negative_ratio,
        "avg_sentiment": avg_sentiment,
        "repeat_ratio": repeat_ratio,
        "quick_evaluation": quick,
        "full_report": report,
        "pipeline_warning": None,
    }


def _get_job_row(conn: Any, job_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM analysis_jobs WHERE id = ? LIMIT 1",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    for key in ("result_payload", "metrics_json"):
        if data.get(key):
            try:
                data[key] = json.loads(data[key])
            except json.JSONDecodeError:
                data[key] = None
    data["cache_hit"] = bool(data.get("cache_hit", 0))
    return data


def insert_items(items: Iterable[IngestItem], *, connection=None) -> Tuple[int, int]:
    inserted = 0
    duplicates = 0
    sql = """
        INSERT INTO items (
            url, title, text, ts, source, lang, channel, author, tags, meta,
            url_hash, content_hash, created_at
        ) VALUES (
            :url, :title, :text, :ts, :source, :lang, :channel, :author, :tags, :meta,
            :url_hash, :content_hash, :created_at
        ) ON CONFLICT(url_hash) DO NOTHING
    """
    with (get_conn() if connection is None else nullcontext(connection)) as conn:
        for item in items:
            row = _row_from_item(item)
            cur = conn.execute(sql, row)
            if cur.rowcount == 1:
                inserted += 1
                _upsert_embedding(
                    conn, int(cur.lastrowid), row.get("title"), row.get("text")
                )
            else:
                duplicates += 1
    return inserted, duplicates


def _read_upload(file: UploadFile) -> str:
    raw = file.file.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Only UTF-8 files are supported.")


def _load_items_from_csv(text: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    rows: List[Dict[str, Any]] = []
    for row in reader:
        cleaned = {k: (v if v != "" else None) for k, v in row.items()}
        rows.append(cleaned)
    return rows


def _load_items_from_json(text: str) -> List[Dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        payload = json.loads(stripped)
        if not isinstance(payload, list):
            raise HTTPException(status_code=400, detail="JSON array expected.")
        return payload
    if stripped.startswith("{"):
        payload = json.loads(stripped)
        if isinstance(payload, dict) and "items" in payload:
            items = payload["items"]
            if not isinstance(items, list):
                raise HTTPException(status_code=400, detail="`items` must be a list.")
            return items
        return [payload]
    raise HTTPException(status_code=400, detail="Unsupported JSON format.")


def _load_items_from_jsonl(text: str) -> List[Dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []
    items: List[Dict[str, Any]] = []
    for line in stripped.splitlines():
        if not line.strip():
            continue
        items.append(json.loads(line))
    return items


# 以下函数用于辅助“后端->逻辑”的接口实现
def _safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _load_items_for_export(
    conn: Any,
    from_ts: Optional[int],
    to_ts: Optional[int],
    limit_rows: int,
) -> List[Dict[str, Any]]:
    where_parts = ["1=1"]
    params: List[Any] = []
    if from_ts is not None:
        where_parts.append("ts >= ?")
        params.append(from_ts)
    if to_ts is not None:
        where_parts.append("ts <= ?")
        params.append(to_ts)

    where_sql = " AND ".join(where_parts)
    sql = f"""
        SELECT id, url, title, text, ts, source, lang, channel, author, tags, meta, created_at
        FROM items
        WHERE {where_sql}
        ORDER BY ts ASC, id ASC
        LIMIT ?
    """
    params.append(limit_rows)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def _shape_export_rows(rows: List[Dict[str, Any]], view: str) -> List[Dict[str, Any]]:
    shaped: List[Dict[str, Any]] = []
    for r in rows:
        title = _clean_optional_str(r.get("title")) or ""
        text = _clean_optional_str(r.get("text")) or title

        tags = _safe_json_loads(r.get("tags"))
        meta = _safe_json_loads(r.get("meta"))

        if view == "analysis":
            shaped.append(
                {
                    "id": r.get("id"),
                    "title": title,
                    "url": r.get("url"),
                    "analysis_text": text,
                    "text": text,
                    "channel": _clean_optional_str(r.get("channel")) or "unknown",
                    "ts": r.get("ts"),
                    "source": r.get("source"),
                    "lang": r.get("lang"),
                    "author": r.get("author"),
                    "tags": tags,
                    "meta": meta,
                }
            )
        else:  # raw
            shaped.append(
                {
                    "id": r.get("id"),
                    "url": r.get("url"),
                    "title": title,
                    "text": text,
                    "ts": r.get("ts"),
                    "source": r.get("source"),
                    "lang": r.get("lang"),
                    "channel": r.get("channel"),
                    "author": r.get("author"),
                    "tags": tags,
                    "meta": meta,
                    "created_at": r.get("created_at"),
                }
            )
    return shaped


def _to_jsonl(items: List[Dict[str, Any]]) -> str:
    return "\n".join(json.dumps(x, ensure_ascii=False) for x in items)


def _to_csv(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    buf = io.StringIO()
    fieldnames = list(items[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in items:
        safe_row = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                safe_row[k] = json.dumps(v, ensure_ascii=False)
            else:
                safe_row[k] = v
        writer.writerow(safe_row)
    return buf.getvalue()


def _host_is_private(host: str) -> bool:
    h = (host or "").lower().strip()
    if not h:
        return False
    if h in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if h.endswith(".local"):
        return True
    if h.startswith("10."):
        return True
    if h.startswith("192.168."):
        return True
    if h.startswith("172."):
        # 172.16.0.0 - 172.31.255.255
        parts = h.split(".")
        if len(parts) >= 2 and parts[1].isdigit():
            sec = int(parts[1])
            if 16 <= sec <= 31:
                return True
    return False


def _is_internal_url(url: str) -> bool:
    try:
        u = urlparse(url)
        return _host_is_private(u.hostname or "")
    except Exception:
        return False


def _is_auth_like(url: str, title: Optional[str] = None) -> bool:
    s = f"{url} {title or ''}".lower()
    keywords = [
        "login",
        "signin",
        "sign-in",
        "signup",
        "sign-up",
        "oauth",
        "authorize",
        "sso",
        "callback",
        "logout",
        "register",
        "captcha",
        "verify",
        "auth",
    ]
    return any(k in s for k in keywords)


def _is_search_like(url: str, title: Optional[str] = None) -> bool:
    s = f"{url} {title or ''}".lower()
    keywords = [
        "/search",
        "search?",
        "q=",
        "query=",
        "code search results",
        "google search",
    ]
    return any(k in s for k in keywords)


def _compress_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _prepare_training_rows(
    rows: List[Dict[str, Any]],
    *,
    label_field: str,
    exclude_internal: bool,
    exclude_auth_pages: bool,
    exclude_search_pages: bool,
    dedup_by_input: bool,
    max_input_len: int,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for r in rows:
        url = str(r.get("url") or "")
        title = _clean_optional_str(r.get("title")) or ""
        text = _clean_optional_str(r.get("text")) or title

        if exclude_internal and _is_internal_url(url):
            continue
        if exclude_auth_pages and _is_auth_like(url, title):
            continue
        if exclude_search_pages and _is_search_like(url, title):
            continue

        input_text = _compress_ws(text)
        if not input_text:
            continue
        if len(input_text) > max_input_len:
            input_text = input_text[:max_input_len]

        label = _clean_optional_str(r.get(label_field)) or "unknown"

        if dedup_by_input:
            key = normalize_text("", input_text)
            if key in seen:
                continue
            seen.add(key)

        out.append(
            {
                "input": input_text,
                "label": label,
                "ts": r.get("ts"),
                "url": url,
                "title": title,
                "source": r.get("source"),
            }
        )
    return out


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with process_ownership(db.DB_PATH):
        _app.state.security = LocalSecurity.load(db.DB_PATH)
        _app.state.operation_lock = asyncio.Lock()
        _app.state.analysis_lock = asyncio.Lock()
        _app.state.request_slots = asyncio.Semaphore(4)
        init_db(_schema_path())
        yield


app = FastAPI(
    title="Information Diet Manager (MVP)",
    lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
)

@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request, exc: RequestValidationError):
    # Do not echo page text, invalid JSON values or exception contexts from a request.
    details = [{"type": error["type"], "loc": error["loc"], "msg": error["msg"]}
               for error in exc.errors()]
    return Response(json.dumps({"detail": details}, ensure_ascii=True),
                    status_code=422, media_type="application/json")


app.add_middleware(LocalAccessMiddleware)
install_data_routes(app, insert_items)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/session")
def session(request: Request):
    return {"role": request.scope["idm_role"]}


@app.post("/collect", response_model=IngestAck)
def collect(item: IngestItem) -> IngestAck:
    inserted, duplicates = insert_items([item])
    return IngestAck(inserted=inserted, duplicates=duplicates, failed=0)


@app.post("/import", response_model=IngestAck)
def import_items(file: UploadFile = File(...)) -> IngestAck:
    filename = (file.filename or "").lower()
    text = _read_upload(file)
    failed = 0
    raw_items: List[Dict[str, Any]] = []
    try:
        if filename.endswith(".csv"):
            raw_items = _load_items_from_csv(text)
        elif filename.endswith(".jsonl"):
            raw_items = _load_items_from_jsonl(text)
        elif filename.endswith(".json"):
            raw_items = _load_items_from_json(text)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type.")
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid file contents: {exc}"
        ) from exc
    items: List[IngestItem] = []
    for raw in raw_items:
        try:
            items.append(_prepare_item(raw))
        except Exception:
            failed += 1
    inserted, duplicates = insert_items(items)
    return IngestAck(inserted=inserted, duplicates=duplicates, failed=failed)


@app.get("/items")
def list_items(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    limit: Optional[int] = Query(None, ge=1, le=200),
    pagination: str = Query("offset", pattern="^(offset|cursor)$"),
    cursor: Optional[str] = Query(None, min_length=1, max_length=2048),
) -> Dict[str, Any]:
    if cursor is not None and pagination != "cursor":
        raise HTTPException(400, detail="A cursor requires pagination=cursor.")
    if pagination == "cursor" and (page != 1 or limit is not None):
        raise HTTPException(400, detail="Cursor pagination uses page_size and cursor only.")
    if limit is not None:
        page_size = limit
    offset = (page - 1) * page_size
    with get_conn() as conn:
        conn.execute("BEGIN")
        if pagination == "cursor":
            rows, metadata = read_cursor_page(conn, page_size=page_size, cursor=cursor)
        else:
            # Legacy page-number clients retain their contract, but each count
            # and page now share a SQLite snapshot within this request.
            total = conn.execute("SELECT COUNT(*) AS cnt FROM items").fetchone()["cnt"]
            rows = conn.execute(
                "SELECT * FROM items ORDER BY id DESC LIMIT ? OFFSET ?",
                (page_size, min(offset, 2**63 - 1)),
            ).fetchall()
            metadata = {"page": page, "page_size": page_size, "total": total}
    items: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = dict(row)
        if item.get("tags"):
            try:
                item["tags"] = json.loads(item["tags"])
            except json.JSONDecodeError:
                item["tags"] = None
        if item.get("meta"):
            try:
                item["meta"] = json.loads(item["meta"])
            except json.JSONDecodeError:
                item["meta"] = None
        items.append(item)
    return {**metadata, "items": items}


def _global_statistics(*, force: bool, backfill_limit: int, record_run: bool) -> Dict[str, Any]:
    """Whole-library page counts, never windowed model scores.

    stats_daily is a compatibility projection only: old releases wrote arbitrary
    analysis windows there. Only explicitly versioned global run payloads can
    be reused, and all state/count/cache reads share a short write transaction.
    """
    day = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item_state = _item_dataset_state(conn)
        max_created_at = int(item_state["max_created_at"])
        data_version = _stable_hash_payload(item_state)
        existing = conn.execute(
            "SELECT payload FROM analysis_runs WHERE day = ? ORDER BY id DESC LIMIT 1",
            (day,),
        ).fetchone()
        payload = None
        if not force and existing is not None:
            try:
                candidate = json.loads(existing["payload"])
            except (TypeError, json.JSONDecodeError):
                candidate = None
            if (isinstance(candidate, dict)
                    and candidate.get("statistics_scope") == "all_saved_pages"
                    and candidate.get("statistics_version") == 1
                    and candidate.get("data_version") == data_version
                    and candidate.get("total_count") == item_state["total_count"]):
                payload = {**candidate, "cached": True, "embeddings_backfilled": 0}

        if payload is None:
            backfilled = _backfill_missing_embeddings(conn, limit=backfill_limit) if backfill_limit > 0 else 0
            content = conn.execute(
                "SELECT COUNT(*) AS measured, COUNT(DISTINCT content_hash) AS distinct_count "
                "FROM items WHERE content_hash IS NOT NULL AND content_hash != ''"
            ).fetchone()
            sample_count = int(content["measured"])
            repeat_ratio = ((sample_count - int(content["distinct_count"])) / sample_count
                            if sample_count else None)
            channel_rows = conn.execute("SELECT channel, COUNT(*) AS cnt FROM items GROUP BY channel").fetchall()
            channel_counts = {key: 0 for key in CHANNEL_CANONICAL_KEYS}
            for row in channel_rows:
                channel_counts[_canonicalize_channel_key(row["channel"])] += int(row["cnt"])
            payload = {
                "day": day,
                "total_count": int(item_state["total_count"]),
                "channel_counts": channel_counts,
                "repeat_ratio": repeat_ratio,
                "repeat_sample_count": sample_count,
                "repeat_metric": "duplicate_normalized_content_hash_fraction",
                "negative_ratio": None,
                "avg_sentiment": None,
                "generated_at": _now_ms(),
                "cached": False,
                "embeddings_backfilled": backfilled,
                "statistics_scope": "all_saved_pages",
                "statistics_version": 1,
                "data_version": data_version,
            }

        # Rewrite even a cache hit: this repairs old/mixed daily projections
        # without ever interpreting them as trustworthy global statistics.
        conn.execute(
            """
            INSERT INTO stats_daily (
                day, total_count, channel_counts, repeat_ratio, negative_ratio,
                avg_sentiment, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                total_count=excluded.total_count,
                channel_counts=excluded.channel_counts,
                repeat_ratio=excluded.repeat_ratio,
                negative_ratio=excluded.negative_ratio,
                avg_sentiment=excluded.avg_sentiment,
                updated_at=excluded.updated_at
            """,
            (
                day,
                payload["total_count"],
                _as_json(payload["channel_counts"]),
                payload["repeat_ratio"],
                None,
                None,
                payload["generated_at"],
                payload["generated_at"],
            ),
        )
        if record_run or not payload["cached"]:
            conn.execute(
                """
                INSERT INTO analysis_runs (
                    day, total_count, channel_counts, repeat_ratio, negative_ratio, avg_sentiment,
                    payload, cached, item_max_created_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (day, payload["total_count"], _as_json(payload["channel_counts"]), payload["repeat_ratio"],
                 None, None, _as_json(payload), int(payload["cached"]), max_created_at, _now_ms()),
            )
        return payload


@app.post("/analyze/run")
def run_analysis(
    force: bool = Query(False),
    backfill_limit: int = Query(2000, ge=0, le=20000),
) -> Dict[str, Any]:
    return _global_statistics(force=force, backfill_limit=backfill_limit, record_run=True)


@app.get("/analyze/history")
def analyze_history(limit: int = Query(20, ge=1, le=200)) -> Dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, day, total_count, channel_counts, repeat_ratio, negative_ratio, avg_sentiment,
                   cached, item_max_created_at, created_at
            FROM analysis_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    runs: List[Dict[str, Any]] = []
    for row in rows:
        run = dict(row)
        if run.get("channel_counts"):
            try:
                run["channel_counts"] = json.loads(run["channel_counts"])
            except json.JSONDecodeError:
                run["channel_counts"] = None
        run["cached"] = bool(run.get("cached", 0))
        runs.append(run)
    return {"total": len(runs), "runs": runs}


@app.post("/analyze/run_full")
def run_full_analysis(
    force: bool = Query(False),
    from_ts: Optional[int] = Query(None, ge=0, le=MAX_INGEST_TS),
    to_ts: Optional[int] = Query(None, ge=0, le=MAX_INGEST_TS),
    limit_rows: int = Query(5000, ge=1, le=50000),
) -> Dict[str, Any]:
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(
            status_code=400, detail="from_ts cannot be greater than to_ts"
        )
    day = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item_state = _item_dataset_state(conn)
        max_created_at = int(item_state["max_created_at"])
        rows = _load_items_for_analysis(
            conn, from_ts=from_ts, to_ts=to_ts, limit_rows=limit_rows
        )
        input_count = len(rows)
        job_key = {
            "day": day,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "limit_rows": limit_rows,
            "mode": "run_full",
            "schema_version": 2,
            "dataset": item_state,
        }
        input_hash = _stable_hash_payload(job_key)

        if not force:
            cached = conn.execute(
                """
                SELECT id, result_payload FROM analysis_jobs
                WHERE input_hash = ? AND item_max_created_at = ? AND status = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (input_hash, max_created_at, JOB_COMPLETED),
            ).fetchone()
            cached_payload: Dict[str, Any] = {}
            if cached:
                if cached["result_payload"]:
                    try:
                        cached_payload = json.loads(cached["result_payload"])
                    except (TypeError, json.JSONDecodeError):
                        cached_payload = {}
            if (cached and isinstance(cached_payload, dict)
                    and cached_payload.get("statistics_scope") == "analysis_window"
                    and cached_payload.get("statistics_version") == 2
                    and cached_payload.get("analysis_status") in {"ready", "empty"}
                    and not cached_payload.get("pipeline_warning")):
                job_id = _insert_analysis_job(
                    conn,
                    status=JOB_COMPLETED,
                    input_hash=input_hash,
                    day=day,
                    from_ts=from_ts,
                    to_ts=to_ts,
                    limit_rows=limit_rows,
                    item_max_created_at=max_created_at,
                    input_count=input_count,
                    cache_hit=True,
                )
                _update_analysis_job(
                    conn,
                    job_id,
                    status=JOB_COMPLETED,
                    result_payload=cached_payload,
                    metrics_json={
                        "cache_reuse_from_job_id": int(cached["id"]),
                        "input_count": input_count,
                        "cache_hit": True,
                    },
                    duration_ms=0,
                    started_at=_now_ms(),
                    finished_at=_now_ms(),
                )
                return {
                    "job_id": job_id,
                    "status": JOB_COMPLETED,
                    "cached": True,
                    "reused_from_job_id": int(cached["id"]),
                    "result": cached_payload,
                }
        # not cached的情况也自然下落到这里
        job_id = _insert_analysis_job(
            conn,
            status=JOB_QUEUED,
            input_hash=input_hash,
            day=day,
            from_ts=from_ts,
            to_ts=to_ts,
            limit_rows=limit_rows,
            item_max_created_at=max_created_at,
            input_count=input_count,
            cache_hit=False,
        )
        started_at = _now_ms()
        _update_analysis_job(
            conn,
            job_id,
            status=JOB_RUNNING,
            started_at=started_at,
        )
        conn.execute("SAVEPOINT legacy_analysis_result")

        try:
            pipeline = _run_lsj_pipeline(rows)
            now_ms = _now_ms()

            channel_counts: Dict[str, int] = {}
            for r in rows:
                key = _canonicalize_channel_key(r.get("channel"))
                channel_counts[key] = channel_counts.get(key, 0) + 1
            channel_counts = _normalize_channel_counts(channel_counts) or {
                key: 0 for key in CHANNEL_CANONICAL_KEYS
            }

            # Windowed experimental metrics belong only to their run/job.
            # stats_daily is reserved for whole-library basic page statistics.
            payload = {
                "day": day,
                "statistics_scope": "analysis_window",
                "statistics_version": 2,
                "analysis_status": "ready" if input_count else "empty",
                "repeat_metric": "legacy_adjacent_text_similarity_threshold_fraction",
                "window": {"from_ts": from_ts, "to_ts": to_ts, "limit_rows": limit_rows,
                           "input_count": input_count},
                "total_count": int(pipeline["input_count"]),
                "channel_counts": channel_counts,
                "category_counts": pipeline["category_counts"],
                "sentiment_counts": pipeline["sentiment_counts"],
                "comparison_count": pipeline["comparison_count"],
                "sentiment_count": pipeline["sentiment_count"],
                "polarity_count": pipeline["polarity_count"],
                "repeat_ratio": pipeline["repeat_ratio"],
                "negative_ratio": pipeline["negative_ratio"],
                "avg_sentiment": pipeline["avg_sentiment"],
                "quick_evaluation": pipeline["quick_evaluation"],
                "full_report": pipeline["full_report"],
                "pipeline_warning": pipeline.get("pipeline_warning"),
                "generated_at": now_ms,
                "cached": False,
            }
            conn.execute(
                """
                INSERT INTO analysis_runs (
                    day, total_count, channel_counts, repeat_ratio, negative_ratio, avg_sentiment,
                    payload, cached, item_max_created_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day,
                    int(pipeline["input_count"]),
                    _as_json(channel_counts),
                    payload["repeat_ratio"],
                    payload["negative_ratio"],
                    payload["avg_sentiment"],
                    _as_json(payload),
                    0,
                    max_created_at,
                    now_ms,
                ),
            )
            duration_ms = _now_ms() - started_at
            _update_analysis_job(
                conn,
                job_id,
                status=JOB_COMPLETED,
                result_payload=payload,
                metrics_json={
                    "cache_hit": False,
                    "input_count": input_count,
                    "duration_ms": duration_ms,
                },
                duration_ms=duration_ms,
                finished_at=_now_ms(),
            )
            conn.execute("RELEASE legacy_analysis_result")
            return {
                "job_id": job_id,
                "status": JOB_COMPLETED,
                "cached": False,
                "result": payload,
            }
        except AnalysisUnavailableError:
            conn.execute("ROLLBACK TO legacy_analysis_result")
            conn.execute("RELEASE legacy_analysis_result")
            message = "Experimental analysis is unavailable; check the local analysis dependencies and configuration."
            _update_analysis_job(
                conn, job_id, status=JOB_FAILED, error=message,
                duration_ms=_now_ms() - started_at, finished_at=_now_ms(),
            )
            # Returning commits the failed job. Raising here would roll it back.
            return JSONResponse(status_code=503, content={"job_id": job_id, "status": JOB_FAILED, "detail": message})
        except ValueError:
            conn.execute("ROLLBACK TO legacy_analysis_result")
            conn.execute("RELEASE legacy_analysis_result")
            # Legacy scoring still requires complete rows. Missing measurements
            # (including the first row's predecessor) are not synthetic zeroes.
            # Persist an explicit rejected job rather than turning this expected
            # validation outcome into HTTP 500 or a fabricated successful score.
            message = "Legacy scoring lacks complete valid measurements; use /dashboard/visualization for partial statistics."
            _update_analysis_job(
                conn, job_id, status=JOB_FAILED, error=message,
                duration_ms=_now_ms() - started_at, finished_at=_now_ms(),
            )
            return JSONResponse(status_code=422, content={"job_id": job_id, "status": JOB_FAILED, "detail": message})
        except Exception:
            conn.execute("ROLLBACK TO legacy_analysis_result")
            conn.execute("RELEASE legacy_analysis_result")
            message = "Legacy analysis failed; no valid result was produced."
            _update_analysis_job(
                conn,
                job_id,
                status=JOB_FAILED,
                error=message,
                metrics_json={
                    "cache_hit": False,
                    "input_count": input_count,
                    "duration_ms": _now_ms() - started_at,
                },
                duration_ms=_now_ms() - started_at,
                finished_at=_now_ms(),
            )
            return JSONResponse(status_code=500, content={"job_id": job_id, "status": JOB_FAILED, "detail": message})


@app.get("/analyze/jobs/{job_id}")
def get_analyze_job(job_id: int) -> Dict[str, Any]:
    with get_conn() as conn:
        row = _get_job_row(conn, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return row


@app.get("/analyze/result/{job_id}")
def get_analyze_result(job_id: int) -> Dict[str, Any]:
    with get_conn() as conn:
        row = _get_job_row(conn, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    if row.get("status") != JOB_COMPLETED:
        raise HTTPException(
            status_code=409, detail=f"job not completed: {row.get('status')}"
        )
    return {
        "job_id": row["id"],
        "status": row["status"],
        "result": row.get("result_payload"),
        "metrics": row.get("metrics_json"),
        "cache_hit": row.get("cache_hit"),
    }


@app.get("/dashboard/summary")
def dashboard_summary() -> Dict[str, Any]:
    return _global_statistics(force=False, backfill_limit=2000, record_run=False)


# 以下路由用于“后端->逻辑”的接口
def _require_analysis_revision(conn: Any, expected: Dict[str, Any], *, job_id: Optional[int] = None) -> None:
    actual = dict(conn.execute("SELECT database_id, revision FROM items_revision WHERE singleton = 1").fetchone())
    if actual != expected:
        if job_id is not None:
            # A delete/restore may already have removed this job: UPDATE never
            # recreates it. Direct external updates can leave it present; finish
            # that job before raising so it cannot remain RUNNING indefinitely.
            _update_analysis_job(conn, job_id, status=JOB_FAILED,
                                 error="analysis snapshot expired", finished_at=_now_ms())
            conn.commit()
        raise HTTPException(409, detail={
            "code": "analysis_snapshot_expired",
            "message": "Records changed during analysis; run analysis again.",
        })


@app.get("/dashboard/visualization")
def dashboard_visualization(
    days: int = Query(DEFAULT_VIS_DAYS, ge=1, le=90),
    from_ts: Optional[int] = Query(None, ge=0, le=MAX_INGEST_TS),
    to_ts: Optional[int] = Query(None, ge=0, le=MAX_INGEST_TS),
    limit_rows: int = Query(5000, ge=1, le=50000),
    force: bool = Query(False),
) -> Dict[str, Any]:
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(
            status_code=400, detail="from_ts cannot be greater than to_ts"
        )

    now_ms = _now_ms()
    resolved_to_ts = to_ts if to_ts is not None else now_ms
    resolved_from_ts = (
        from_ts
        if from_ts is not None
        else max(0, resolved_to_ts - days * 24 * 60 * 60 * 1000)
    )
    if resolved_from_ts > resolved_to_ts:
        raise HTTPException(400, detail="from_ts cannot be greater than the resolved to_ts")

    day = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        # Keep the coverage count and selected rows in the same read snapshot.
        conn.execute("BEGIN")
        available_count = conn.execute(
            "SELECT COUNT(*) FROM items WHERE ts >= ? AND ts <= ?",
            (resolved_from_ts, resolved_to_ts),
        ).fetchone()[0]
        item_state = conn.execute(
            "SELECT COALESCE(MAX(created_at), 0) AS max_created_at, COALESCE(MAX(id), 0) AS max_id FROM items"
        ).fetchone()
        revision = dict(conn.execute("SELECT database_id, revision FROM items_revision WHERE singleton = 1").fetchone())
        max_created_at = int(item_state["max_created_at"] or 0)
        rows = _load_items_for_analysis(
            conn,
            from_ts=resolved_from_ts,
            to_ts=resolved_to_ts,
            limit_rows=limit_rows,
        )
        input_count = len(rows)
        job_key = {
            "days": days,
            "from_ts": resolved_from_ts,
            "to_ts": resolved_to_ts,
            "limit_rows": limit_rows,
            "mode": "dashboard_visualization",
            "schema_version": 3,
            "data_revision": revision,
            "max_id": int(item_state["max_id"]),
        }
        input_hash = _stable_hash_payload(job_key)

    # Release the read snapshot before writing job/cache metadata.
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_analysis_revision(conn, revision)
        if not force:
            cached = conn.execute(
                """
                SELECT id, result_payload FROM analysis_jobs
                WHERE input_hash = ? AND item_max_created_at = ? AND status = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (input_hash, max_created_at, JOB_COMPLETED),
            ).fetchone()
            if cached:
                cached_payload: Dict[str, Any] = {}
                if cached["result_payload"]:
                    try:
                        cached_payload = json.loads(cached["result_payload"])
                    except json.JSONDecodeError:
                        cached_payload = {}
                if (
                    isinstance(cached_payload, dict)
                    and cached_payload.get("analysis_status") == "ready"
                    and not cached_payload.get("pipeline_warning")
                ):
                    job_id = _insert_analysis_job(
                        conn,
                        status=JOB_COMPLETED,
                        input_hash=input_hash,
                        day=day,
                        from_ts=resolved_from_ts,
                        to_ts=resolved_to_ts,
                        limit_rows=limit_rows,
                        item_max_created_at=max_created_at,
                        input_count=input_count,
                        cache_hit=True,
                    )
                    _update_analysis_job(
                        conn,
                        job_id,
                        status=JOB_COMPLETED,
                        result_payload=cached_payload,
                        metrics_json={
                            "cache_reuse_from_job_id": int(cached["id"]),
                            "input_count": input_count,
                            "cache_hit": True,
                            "mode": "dashboard_visualization",
                        },
                        duration_ms=0,
                        started_at=_now_ms(),
                        finished_at=_now_ms(),
                    )
                    cached_payload["cached"] = True
                    cached_payload["reused_from_job_id"] = int(cached["id"])
                    return cached_payload

        job_id = _insert_analysis_job(
            conn,
            status=JOB_RUNNING,
            input_hash=input_hash,
            day=day,
            from_ts=resolved_from_ts,
            to_ts=resolved_to_ts,
            limit_rows=limit_rows,
            item_max_created_at=max_created_at,
            input_count=input_count,
            cache_hit=False,
        )

    # Optional inference must not hold SQLite's writer lock and block collection
    # or deletion. Destructive changes invalidate the result before publication.
    started_at = _now_ms()
    payload = _build_visualization_result(
        rows,
        from_ts=resolved_from_ts,
        to_ts=resolved_to_ts,
        limit_rows=limit_rows,
    )
    payload["window"]["available_count"] = int(available_count)
    payload["window"]["truncated"] = available_count > len(rows)
    payload["cached"] = False
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_analysis_revision(conn, revision, job_id=job_id)
        _update_analysis_job(
            conn,
            job_id,
            status=JOB_FAILED if payload["analysis_status"] in {"failed", "unavailable"} else JOB_COMPLETED,
            result_payload=payload,
            metrics_json={
                "input_count": input_count,
                "cache_hit": False,
                "mode": "dashboard_visualization",
                "pipeline_warning": payload.get("pipeline_warning"),
            },
            duration_ms=_now_ms() - started_at,
            started_at=started_at,
            finished_at=_now_ms(),
        )
        return payload


@app.get("/export/lsj")
def export_lsj(
    from_ts: Optional[int] = Query(None, ge=0, le=MAX_INGEST_TS),
    to_ts: Optional[int] = Query(None, ge=0, le=MAX_INGEST_TS),
    limit_rows: int = Query(5000, ge=1, le=200000),
    view: str = Query("analysis", pattern="^(analysis|raw)$"),
    fmt: str = Query("json", pattern="^(json|jsonl|csv)$"),
) -> Any:
    """
    导出给 lsj 使用：
    - view=analysis: 含 analysis_text/channel/ts（推荐给分析）
    - view=raw: 接近原始采集字段
    - fmt=json|jsonl|csv
    """
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(
            status_code=400, detail="from_ts cannot be greater than to_ts"
        )

    with get_conn() as conn:
        rows = _load_items_for_export(
            conn, from_ts=from_ts, to_ts=to_ts, limit_rows=limit_rows
        )

    items = _shape_export_rows(rows, view=view)

    if fmt == "json":
        return {
            "count": len(items),
            "view": view,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "items": items,
        }

    if fmt == "jsonl":
        content = _to_jsonl(items)
        return StreamingResponse(
            io.StringIO(content),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="lsj_export_{view}.jsonl"'
            },
        )

    # csv
    content = _to_csv(items)
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="lsj_export_{view}.csv"'
        },
    )


@app.get("/export/lsj/training")
def export_lsj_training(
    from_ts: Optional[int] = Query(None, ge=0, le=MAX_INGEST_TS),
    to_ts: Optional[int] = Query(None, ge=0, le=MAX_INGEST_TS),
    limit_rows: int = Query(5000, ge=1, le=200000),
    label_field: str = Query("channel", pattern="^(channel|source)$"),
    fmt: str = Query("json", pattern="^(json|jsonl|csv)$"),
    # 新增参数
    bare: bool = Query(True, description="json格式时仅返回数组，适配 data_cleaning.py"),
    exclude_internal: bool = Query(
        True, description="过滤 localhost/127.0.0.1/内网地址"
    ),
    exclude_auth_pages: bool = Query(True, description="过滤登录/oauth/授权回调等页面"),
    exclude_search_pages: bool = Query(False, description="过滤搜索结果页"),
    dedup_by_input: bool = Query(True, description="按 input 去重"),
    max_input_len: int = Query(1000, ge=50, le=5000),
) -> Any:
    """
    导出为 data_cleaning.py 友好结构:
    [{input, label, ts, url, title, source}]
    """
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(
            status_code=400, detail="from_ts cannot be greater than to_ts"
        )

    with get_conn() as conn:
        rows = _load_items_for_export(
            conn, from_ts=from_ts, to_ts=to_ts, limit_rows=limit_rows
        )

    items = _prepare_training_rows(
        rows,
        label_field=label_field,
        exclude_internal=exclude_internal,
        exclude_auth_pages=exclude_auth_pages,
        exclude_search_pages=exclude_search_pages,
        dedup_by_input=dedup_by_input,
        max_input_len=max_input_len,
    )

    if fmt == "json":
        if bare:
            return items
        return {
            "count": len(items),
            "label_field": label_field,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "items": items,
        }

    if fmt == "jsonl":
        content = _to_jsonl(items)
        return StreamingResponse(
            io.StringIO(content),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": 'attachment; filename="lsj_training.jsonl"'
            },
        )

    content = _to_csv(items)
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="lsj_training.csv"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
