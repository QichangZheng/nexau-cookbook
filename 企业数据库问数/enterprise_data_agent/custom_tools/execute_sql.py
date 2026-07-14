"""Execute SQL tool — runs read-only SELECT queries directly against a local SQLite file."""

from __future__ import annotations

import json
import logging
import re
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_ROWS = 10
MAX_OUTPUT_LENGTH = 50000
DEFAULT_TIMEOUT = 30

_DANGEROUS_KEYWORDS = (
    "DROP", "TRUNCATE", "DELETE", "ALTER", "CREATE", "INSERT", "UPDATE",
    "REPLACE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
    "GRANT", "REVOKE",
)


def _strip_sql_comments(sql: str) -> str:
    no_line = re.sub(r"--[^\n]*", "", sql)
    no_block = re.sub(r"/\*.*?\*/", "", no_line, flags=re.DOTALL)
    return no_block


def _coerce_value(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def execute_sql(
    sql: str,
    db_path: str,
    timeout: int | None = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Execute a read-only SQL query against a local SQLite database file.

    Args:
        sql: SQL query (SELECT / WITH ... SELECT only).
        db_path: Path to the SQLite database file on the local filesystem.
        timeout: Query timeout in seconds (default 30).
        max_rows: Max rows to return (default 10).
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    if max_rows is None:
        max_rows = MAX_ROWS

    if not sql or not sql.strip():
        return {"status": "error", "error": "SQL query cannot be empty"}

    sql_cleaned_upper = _strip_sql_comments(sql).strip().upper()
    for keyword in _DANGEROUS_KEYWORDS:
        if re.match(rf"^{keyword}(?:\s|$)", sql_cleaned_upper):
            return {
                "status": "error",
                "error": f"Only SELECT queries are allowed. Found: {keyword}",
                "sql": sql,
            }
    if not re.match(r"^(SELECT|WITH)\b", sql_cleaned_upper):
        return {
            "status": "error",
            "error": "Only SELECT or WITH ... SELECT queries are allowed.",
            "sql": sql,
        }

    db_file = Path(db_path)
    if not db_file.exists():
        cwd = Path.cwd()
        candidates: list[str] = []
        search_roots = [cwd, cwd.parent, Path("/app"), Path("/workspace"), Path("/artifacts"), Path("/tmp")]
        seen: set[str] = set()
        for root in search_roots:
            if not root.exists() or str(root) in seen:
                continue
            seen.add(str(root))
            try:
                for found in root.rglob("*.sqlite*"):
                    candidates.append(str(found))
                    if len(candidates) >= 20:
                        break
            except (PermissionError, OSError):
                continue
            if len(candidates) >= 20:
                break

        try:
            cwd_listing = sorted(os.listdir(cwd))[:50]
        except OSError as e:
            cwd_listing = [f"<listdir error: {e}>"]

        return {
            "status": "error",
            "error": f"Database file not found: {db_path}",
            "sql": sql,
            "diagnostics": {
                "db_path_arg": db_path,
                "resolved_abs_path": str(db_file.resolve()),
                "cwd": str(cwd),
                "cwd_entries": cwd_listing,
                "sqlite_candidates": candidates or ["<none found>"],
                "search_roots": [str(r) for r in search_roots if r.exists()],
            },
        }

    start = time.time()
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return {
            "status": "error",
            "error": f"Failed to open database: {e}",
            "sql": sql,
        }

    conn.row_factory = sqlite3.Row
    deadline = time.time() + timeout
    conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 1000)

    try:
        cursor = conn.execute(sql)
        cols = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        total = len(rows)
        truncated = total > max_rows
        data = [{k: _coerce_value(v) for k, v in dict(r).items()} for r in rows[:max_rows]]
        duration_ms = int((time.time() - start) * 1000)

        result: dict[str, Any] = {
            "status": "success",
            "command_status": f"SELECT {total}",
            "sql": sql,
            "columns": cols,
            "data": data,
            "row_count": len(data),
            "total_rows": total,
            "truncated": truncated,
            "duration_ms": duration_ms,
        }

        warnings = []
        if total == 0:
            warnings.append("Query returned 0 rows.")
        if truncated:
            warnings.append("Results truncated due to row limit.")
        if warnings:
            result["warnings"] = warnings
    except sqlite3.OperationalError as e:
        duration_ms = int((time.time() - start) * 1000)
        msg = str(e)
        if "interrupted" in msg.lower():
            result = {
                "status": "timeout",
                "error": f"Query timed out after {timeout}s",
                "sql": sql,
                "duration_ms": duration_ms,
            }
        else:
            result = {
                "status": "error",
                "error": msg,
                "error_type": "OperationalError",
                "sql": sql,
                "duration_ms": duration_ms,
            }
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        result = {
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__,
            "sql": sql,
            "duration_ms": duration_ms,
        }
    finally:
        conn.close()

    if len(json.dumps(result, ensure_ascii=False)) > MAX_OUTPUT_LENGTH:
        data = result.get("data", [])
        while len(data) > 1 and len(json.dumps(result, ensure_ascii=False)) > MAX_OUTPUT_LENGTH:
            data = data[:-1]
            result["data"] = data
            result["row_count"] = len(data)
            result["truncated"] = True
        warnings = result.get("warnings", [])
        if not any("length limit" in w for w in warnings):
            warnings.append("Results truncated due to length limit.")
            result["warnings"] = warnings

    logger.info("SQL executed: status=%s db=%s", result.get("status"), db_path)
    return result
