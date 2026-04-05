"""
class_router.py — KI-Modell-Team: 6 Klassen mit 3-Stufen-Fallback
==================================================================
Jede Klasse hat 3 Tier:
  Tier 1: Free Premium (tägliches Limit)
  Tier 2: Free Backup  (tägliches Limit)
  Tier 3: Paid         (kein Limit, immer verfügbar)

Usage-Tracking per Klasse läuft über Redis-Keys mit täglichem TTL.
"""

import os
from datetime import datetime, timezone
from typing import Optional

# ─── Modell-Klassen ──────────────────────────────────────────────────────────

MODEL_CLASSES: dict[str, list[dict]] = {
    # ── Coding-Klassen (nur IDE/Cline, nicht im OWT-Dropdown) ───────────────
    "titan": [
        {"model": os.getenv("TITAN_FREE1", "nousresearch/hermes-3-llama-3.1-405b:free"), "limit": 10,  "tools": True},
        {"model": os.getenv("TITAN_FREE2", "openai/gpt-oss-120b:free"),                  "limit": 10,  "tools": True},
        {"model": os.getenv("TITAN_PAID",  "qwen/qwen2.5-72b-instruct"),                 "limit": None, "tools": True},
    ],
    "professional": [
        {"model": os.getenv("PRO_FREE1", "qwen/qwen3-coder:free"),                       "limit": 20,  "tools": True},
        {"model": os.getenv("PRO_FREE2", "meta-llama/llama-3.3-70b-instruct:free"),      "limit": 20,  "tools": True},
        {"model": os.getenv("PRO_PAID",  "deepseek/deepseek-v3.2"),                      "limit": None, "tools": True},
    ],
    "flitzer": [
        {"model": os.getenv("FLITZER_FREE1", "openai/gpt-oss-20b:free"),                 "limit": 50,  "tools": True},
        {"model": os.getenv("FLITZER_FREE2", "google/gemma-3-12b-it:free"),              "limit": 50,  "tools": False},
        {"model": os.getenv("FLITZER_PAID",  "google/gemini-2.5-flash-lite"),            "limit": None, "tools": True},
    ],
    # ── Chat-Klassen (erscheinen im OWT-Dropdown) ───────────────────────────
    "denker": [
        {"model": os.getenv("DENKER_FREE1", "qwen/qwen3-next-80b-a3b-instruct:free"),    "limit": 10,  "tools": False},
        {"model": os.getenv("DENKER_FREE2", "qwen/qwen3.6-plus:free"),                   "limit": 20,  "tools": False},
        {"model": os.getenv("DENKER_PAID",  "qwen/qwen2.5-max"),                         "limit": None, "tools": True},
    ],
    "allrounder": [
        {"model": os.getenv("ALLROUNDER_FREE1", "meta-llama/llama-3.3-70b-instruct:free"), "limit": 50, "tools": True},
        {"model": os.getenv("ALLROUNDER_FREE2", "google/gemma-3-27b-it:free"),             "limit": 50, "tools": False},
        {"model": os.getenv("ALLROUNDER_PAID",  "google/gemini-2.5-flash-lite"),           "limit": None, "tools": True},
    ],
    "begleiter": [
        {"model": os.getenv("BEGLEITER_FREE1", "google/gemma-3-12b-it:free"),            "limit": 100, "tools": False},
        {"model": os.getenv("BEGLEITER_FREE2", "google/gemma-3-4b-it:free"),             "limit": 100, "tools": False},
        {"model": os.getenv("BEGLEITER_PAID",  "google/gemini-2.5-flash-lite"),          "limit": None, "tools": True},
    ],
}

# ─── Alias-Mapping ───────────────────────────────────────────────────────────

# Coding-Klassen: nur IDE/Cline, kein Router-Tool-Loop
CODING_CLASSES: frozenset[str] = frozenset({"titan", "professional", "flitzer"})

ALIASES: dict[str, str] = {
    # Coding-Klassen
    "titan":               "titan",
    "coding-titan":        "titan",
    "professional":        "professional",
    "profi":               "professional",
    "coding-professional": "professional",
    "code-auto":           "professional",   # Roo Code: tool-capable auto-routing
    "flitzer":             "flitzer",
    "coding-flitzer":      "flitzer",
    # Chat-Klassen
    "denker":              "denker",
    "chat-denker":         "denker",
    "allrounder":          "allrounder",
    "chat-allrounder":     "allrounder",
    "begleiter":           "begleiter",
    "chat-begleiter":      "begleiter",
}

# ─── Resolver ────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def resolve_class_chain(class_name: str, redis, need_tools: bool = False) -> list[str]:
    """
    Gibt die vollständige, geordnete Fallback-Kette für eine Klasse zurück:
      - Erst alle Free-Tiers die ihr Tageslimit noch nicht erreicht haben
      - Dann das bezahlte Tier (immer am Ende)

    Damit kann stream_with_fallback die gesamte Kette abarbeiten — auch bei
    Laufzeitfehlern (404, 429, ...) wird Tier 2 vor Tier 3 (Paid) versucht.
    """
    today = _today()
    tiers = MODEL_CLASSES[class_name]
    free_available: list[str] = []
    paid: list[str] = []

    for tier in tiers:
        if need_tools and not tier["tools"]:
            continue
        limit = tier["limit"]
        if limit is None:
            paid.append(tier["model"])
        else:
            used = int(await redis.get(f"model_usage:{tier['model']}:{today}") or 0)
            if used < limit:
                free_available.append(tier["model"])

    # Reihenfolge: verfügbare Free-Tiers → Paid
    chain = free_available + paid
    if not chain:
        # Alle Free erschöpft, kein Paid gefunden → letztes Tier als Notausgang
        chain = [tiers[-1]["model"]]
    return chain


async def resolve_class_model(class_name: str, redis, need_tools: bool = False) -> str:
    """Gibt das primäre (beste verfügbare) Modell zurück."""
    chain = await resolve_class_chain(class_name, redis, need_tools)
    return chain[0]


async def increment_usage(model: str, redis) -> None:
    """Zählt einen Request für das heutige Tages-Limit.
    Key läuft um Mitternacht UTC + 1h Puffer ab."""
    today = _today()
    key   = f"model_usage:{model}:{today}"
    await redis.incr(key)
    now = datetime.now(timezone.utc)
    seconds_left = (
        86400
        - now.hour * 3600
        - now.minute * 60
        - now.second
        + 3600           # 1h Puffer
    )
    await redis.expire(key, seconds_left)


async def get_class_status(redis) -> dict:
    """Gibt Tier-Status + Verbrauch aller Klassen zurück."""
    today  = _today()
    result = {}
    for class_name, tiers in MODEL_CLASSES.items():
        class_status = []
        for i, tier in enumerate(tiers):
            model = tier["model"]
            limit = tier["limit"]
            used: Optional[int] = (
                int(await redis.get(f"model_usage:{model}:{today}") or 0)
                if limit is not None
                else None
            )
            class_status.append({
                "tier":   i + 1,
                "model":  model,
                "limit":  limit,
                "used":   used,
                "free":   limit is not None,
                "active": used is None or used < (limit or 0),
            })
        result[class_name] = class_status
    return result
