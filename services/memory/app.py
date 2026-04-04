"""
Memory Service — Hindsight-compatible REST API
===============================================
Provides persistent semantic memory using SQLite + TF-IDF similarity.
No external ML dependencies required.

Endpoints:
  POST /store           Store a query-response pair
  POST /search          Search for relevant memories
  DELETE /memories/{id} Delete specific memory
  DELETE /memories      Delete all memories
  GET  /stats           Memory statistics
  GET  /health          Health check
"""

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import time
from collections import Counter
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

log = logging.getLogger("memory_svc")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DB_PATH   = os.getenv("MEMORY_DB_PATH", "/data/memory/hindsight.db")
MAX_MEM   = int(os.getenv("MAX_MEMORIES", "10000"))
TOP_K     = int(os.getenv("MEMORY_TOP_K", "5"))
SIM_THRESH = float(os.getenv("SIMILARITY_THRESHOLD", "0.3"))

# ─── SQLite Schema ───────────────────────────────────────────

_SCHEMA_BASE = """
CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    query      TEXT NOT NULL,
    response   TEXT NOT NULL,
    tokens     TEXT NOT NULL,  -- JSON: word frequency map
    metadata   TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_created ON memories(created_at);
"""

def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_BASE)  # table + created_at index only

    # ── migration: add user_id column if DB was created before this change ──
    try:
        conn.execute("ALTER TABLE memories ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")
        conn.commit()
        log.info("Migrated memories table: added user_id column")
    except Exception:
        pass  # column already exists

    # ── create user_id index after column is guaranteed to exist ──
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user ON memories(user_id)")
    conn.commit()
    return conn

# ─── TF-IDF similarity ───────────────────────────────────────

_STOP_WORDS = {
    "a","an","the","is","it","in","on","at","to","and","or","of","for",
    "with","as","by","that","this","was","are","be","been","has","have",
    "do","does","did","but","not","from","what","how","why","which","who",
    "can","will","would","could","should","may","might","shall",
}

def tokenize(text: str) -> Counter:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return Counter(w for w in words if w not in _STOP_WORDS and len(w) > 1)


def cosine_sim(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot   = sum(a[w] * b[w] for w in common)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)

# ─── App ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB is initialized
    with get_db():
        pass
    log.info("Memory service started. DB: %s", DB_PATH)
    yield
    log.info("Memory service stopped.")

app = FastAPI(title="Hindsight Memory Service", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Models ──────────────────────────────────────────────────

class StoreRequest(BaseModel):
    query:    str
    response: str
    user_id:  str = "default"
    metadata: Optional[dict] = None

class SearchRequest(BaseModel):
    query:   str
    limit:   int = 5
    user_id: str = "default"

# ─── Routes ──────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "memory"}

@app.get("/stats")
def stats():
    with get_db() as conn:
        count  = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        oldest = conn.execute("SELECT MIN(created_at) FROM memories").fetchone()[0]
        newest = conn.execute("SELECT MAX(created_at) FROM memories").fetchone()[0]
        by_user = [
            {"user_id": r["user_id"], "count": r["count"]}
            for r in conn.execute(
                "SELECT user_id, COUNT(*) as count FROM memories GROUP BY user_id ORDER BY count DESC"
            ).fetchall()
        ]
    return {
        "total":   count,
        "max":     MAX_MEM,
        "oldest":  oldest,
        "newest":  newest,
        "by_user": by_user,
        "db_path": DB_PATH,
    }

@app.post("/store")
def store(req: StoreRequest):
    mid   = hashlib.sha256(f"{req.query}{time.time()}".encode()).hexdigest()[:16]
    toks  = json.dumps(dict(tokenize(f"{req.query} {req.response}")))
    meta  = json.dumps(req.metadata or {"timestamp": time.time()})

    with get_db() as conn:
        # Enforce max memories globally: evict oldest
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if count >= MAX_MEM:
            oldest = conn.execute(
                "SELECT id FROM memories ORDER BY created_at ASC LIMIT ?",
                (count - MAX_MEM + 1,)
            ).fetchall()
            for row in oldest:
                conn.execute("DELETE FROM memories WHERE id = ?", (row["id"],))

        conn.execute(
            "INSERT INTO memories(id,user_id,query,response,tokens,metadata,created_at) VALUES(?,?,?,?,?,?,?)",
            (mid, req.user_id, req.query[:2000], req.response[:4000], toks, meta, time.time()),
        )
        conn.commit()

    log.debug("Stored memory id=%s user=%s", mid, req.user_id)
    return {"id": mid, "status": "stored"}

@app.post("/search")
def search(req: SearchRequest):
    query_toks = tokenize(req.query)
    limit      = min(req.limit, 20)

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id,query,response,tokens,metadata FROM memories WHERE user_id = ?",
            (req.user_id,),
        ).fetchall()

    scored: list[dict] = []
    for row in rows:
        try:
            mem_toks = Counter(json.loads(row["tokens"]))
        except Exception:
            continue
        score = cosine_sim(query_toks, mem_toks)
        if score >= SIM_THRESH:
            scored.append({
                "id":       row["id"],
                "query":    row["query"],
                "response": row["response"],
                "score":    round(score, 4),
                "metadata": json.loads(row["metadata"] or "{}"),
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"memories": scored[:limit], "total_searched": len(rows)}

@app.delete("/memories/{memory_id}")
def delete_one(memory_id: str):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted", "id": memory_id}

@app.delete("/memories")
def delete_all(user_id: Optional[str] = None):
    """Delete all memories. Scoped to user_id if provided, otherwise all (admin)."""
    with get_db() as conn:
        if user_id:
            count = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
        else:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.execute("DELETE FROM memories")
        conn.commit()
    return {"status": "cleared", "deleted": count, "user_id": user_id or "all"}
