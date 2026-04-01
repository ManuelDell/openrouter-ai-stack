"""
hindsight_client.py — Memory Client
=====================================
Lightweight client für den Memory Service.
Kann standalone oder innerhalb anderer Services verwendet werden.

Features:
  - store()  : Speichert Conversation-Pairs persistent
  - search() : Semantische Suche über gespeicherte Memories
  - forget() : Löscht spezifische oder alle Memories
  - Auto-Retention: läuft als Background-Task im Router
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger("hindsight_client")

MEMORY_URL = os.getenv("MEMORY_SERVICE_URL", "http://localhost:8086")
DEFAULT_TIMEOUT = 5.0


@dataclass
class Memory:
    id: str
    query: str
    response: str
    score: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    @property
    def content(self) -> str:
        return f"Q: {self.query}\nA: {self.response}"


class HindsightClient:
    """
    Async client for the Memory service.

    Usage:
        client = HindsightClient()
        await client.store("What is FastAPI?", "FastAPI is a Python web framework...")
        memories = await client.search("Python web frameworks")
    """

    def __init__(
        self,
        base_url: str = MEMORY_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout

    # ─── Public API ──────────────────────────────────────────

    async def store(
        self,
        query: str,
        response: str,
        metadata: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Store a query-response pair.
        Returns the memory ID or None on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/store",
                    json={
                        "query":    query[:2000],
                        "response": response[:4000],
                        "metadata": metadata or {"timestamp": time.time()},
                    },
                )
                if resp.status_code == 200:
                    return resp.json().get("id")
        except Exception as e:
            log.debug("store() failed (non-fatal): %s", e)
        return None

    async def search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> list[Memory]:
        """
        Search for memories relevant to the query.
        Returns list of Memory objects sorted by relevance.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/search",
                    json={"query": query, "limit": limit},
                )
                if resp.status_code == 200:
                    raw = resp.json().get("memories", [])
                    return [
                        Memory(
                            id       = m["id"],
                            query    = m["query"],
                            response = m["response"],
                            score    = m.get("score", 0.0),
                            metadata = m.get("metadata", {}),
                        )
                        for m in raw
                        if m.get("score", 0.0) >= min_score
                    ]
        except Exception as e:
            log.debug("search() failed (non-fatal): %s", e)
        return []

    async def forget(self, memory_id: Optional[str] = None) -> bool:
        """
        Delete a specific memory by ID, or ALL memories if id is None.
        Returns True on success.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if memory_id:
                    resp = await client.delete(f"{self.base_url}/memories/{memory_id}")
                else:
                    resp = await client.delete(f"{self.base_url}/memories")
                return resp.status_code == 200
        except Exception as e:
            log.debug("forget() failed: %s", e)
        return False

    async def stats(self) -> dict:
        """Return memory service statistics."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.base_url}/stats")
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            log.debug("stats() failed: %s", e)
        return {"error": "unavailable"}

    async def health(self) -> bool:
        """Check if memory service is reachable."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    # ─── Context Builder ─────────────────────────────────────

    async def build_context(self, query: str, limit: int = 5) -> str:
        """
        Build a formatted context string from relevant memories.
        Ready to inject as a system message.
        """
        memories = await self.search(query, limit=limit)
        if not memories:
            return ""
        lines = ["Relevant context from previous sessions:"]
        for m in memories:
            lines.append(f"\n[Memory | score={m.score:.2f}]")
            lines.append(f"  Query:    {m.query[:200]}")
            lines.append(f"  Response: {m.response[:400]}")
        return "\n".join(lines)


# ─── Standalone retention loop ───────────────────────────────

class AutoRetention:
    """
    Background task that auto-stores interactions.
    Attach to any async application.

    Usage:
        retention = AutoRetention(client)
        asyncio.create_task(retention.start())
        # Later:
        retention.add("user question", "assistant answer")
    """

    def __init__(self, client: HindsightClient, batch_size: int = 10) -> None:
        self._client     = client
        self._batch_size = batch_size
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running    = False

    def add(self, query: str, response: str, metadata: Optional[dict] = None) -> None:
        """Non-blocking: queue an interaction for storage."""
        self._queue.put_nowait((query, response, metadata or {}))

    async def start(self) -> None:
        """Run the retention loop until cancelled."""
        self._running = True
        log.info("AutoRetention started (batch_size=%d)", self._batch_size)
        while self._running:
            batch: list[tuple] = []
            try:
                # Wait for first item
                item = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                batch.append(item)
                # Drain up to batch_size without waiting
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                continue

            # Store all items in parallel
            await asyncio.gather(
                *(self._client.store(q, r, m) for q, r, m in batch),
                return_exceptions=True,
            )
            log.debug("AutoRetention stored %d interactions", len(batch))

    async def stop(self) -> None:
        self._running = False


# ─── CLI helper ──────────────────────────────────────────────

async def _cli() -> None:
    """Quick CLI to test the memory service."""
    import sys
    client = HindsightClient()

    if not await client.health():
        print("ERROR: Memory service not reachable at", MEMORY_URL)
        sys.exit(1)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"

    if cmd == "stats":
        print(json.dumps(await client.stats(), indent=2))

    elif cmd == "store" and len(sys.argv) >= 4:
        mid = await client.store(sys.argv[2], sys.argv[3])
        print(f"Stored: id={mid}")

    elif cmd == "search" and len(sys.argv) >= 3:
        memories = await client.search(" ".join(sys.argv[2:]))
        for m in memories:
            print(f"[{m.score:.3f}] {m.query[:80]} → {m.response[:120]}")

    elif cmd == "forget":
        mid = sys.argv[2] if len(sys.argv) > 2 else None
        ok  = await client.forget(mid)
        print("Deleted" if ok else "Failed")

    else:
        print("Usage: hindsight_client.py [stats|store <q> <r>|search <q>|forget [id]]")


if __name__ == "__main__":
    asyncio.run(_cli())
