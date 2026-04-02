"""
cost_tracker.py — SQLite-backed cost storage for OpenRouter API calls.

Single responsibility: persist and query cost data.
All callers use store_cost() to write, query_* functions to read.
"""

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

DB_PATH = os.getenv("COST_DB_PATH", "/data/costs/cost_log.db")


def _ensure_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cost_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        REAL    NOT NULL,
                model            TEXT    NOT NULL,
                feature          TEXT    NOT NULL DEFAULT 'standard',
                sub_type         TEXT,
                tokens_prompt    INTEGER NOT NULL DEFAULT 0,
                tokens_completion INTEGER NOT NULL DEFAULT 0,
                cost             REAL    NOT NULL DEFAULT 0.0,
                latency_ms       INTEGER
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_feat ON cost_log (timestamp, feature)")


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def store_cost(
    model: str,
    tokens_prompt: int,
    tokens_completion: int,
    cost: float,
    feature: str = "standard",
    sub_type: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO cost_log
               (timestamp, model, feature, sub_type, tokens_prompt, tokens_completion, cost, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), model, feature, sub_type, tokens_prompt, tokens_completion, cost, latency_ms),
        )


def query_today() -> dict:
    day_start = time.time() - (time.time() % 86400)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT model, feature, SUM(cost) as cost, SUM(tokens_prompt+tokens_completion) as tokens "
            "FROM cost_log WHERE timestamp >= ? GROUP BY model, feature",
            (day_start,),
        ).fetchall()

    total = sum(r["cost"] for r in rows)
    by_model: dict[str, float] = {}
    by_feature: dict[str, float] = {}
    for r in rows:
        by_model[r["model"]] = round(by_model.get(r["model"], 0) + r["cost"], 6)
        by_feature[r["feature"]] = round(by_feature.get(r["feature"], 0) + r["cost"], 6)

    return {"total": round(total, 6), "by_model": by_model, "by_feature": by_feature}


def query_stats() -> dict:
    now = time.time()
    periods = {"daily": 86400, "weekly": 604800, "monthly": 2592000}
    result = {}
    with _conn() as conn:
        for label, seconds in periods.items():
            row = conn.execute(
                "SELECT COALESCE(SUM(cost), 0) as total FROM cost_log WHERE timestamp >= ?",
                (now - seconds,),
            ).fetchone()
            result[label] = round(row["total"], 6)
    return result


def query_history(days: int = 7) -> list[dict]:
    since = time.time() - days * 86400
    with _conn() as conn:
        rows = conn.execute(
            "SELECT timestamp, model, feature, sub_type, tokens_prompt, tokens_completion, cost, latency_ms "
            "FROM cost_log WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 500",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]


def query_by_feature() -> dict[str, float]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT feature, COALESCE(SUM(cost), 0) as total FROM cost_log GROUP BY feature"
        ).fetchall()
    return {r["feature"]: round(r["total"], 6) for r in rows}


def query_by_model() -> dict[str, float]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT model, COALESCE(SUM(cost), 0) as total FROM cost_log GROUP BY model"
        ).fetchall()
    return {r["model"]: round(r["total"], 6) for r in rows}


# Initialise DB on import
_ensure_db()
