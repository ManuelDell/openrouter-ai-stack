"""
smart_router.py — OpenRouter Smart Routing Service
===================================================
Auto-routes requests to optimal OpenRouter models:
  - Image present        → Qwen3-VL  (vision)
  - Complex / long       → Qwen3-VL  (reasoning)
  - Simple / short       → DeepSeek V3.2 (fast)
  - Any failure          → Gemini 3.1 Flash (fallback)

Exposes an OpenAI-compatible /v1/chat/completions endpoint
so any OpenAI client works out of the box.
"""

import os
import re
import json
import time
import base64
import hashlib
import logging
import asyncio
from typing import Any, AsyncGenerator, Optional

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from utils.cost_tracker import store_cost
from utils.request_analyzer import detect_research_trigger, detect_imagegen_trigger, detect_audio_trigger
from routes.cost_routes import router as cost_router
from dispatchers import research_dispatcher

# ─── Logging ─────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("smart_router")

# ─── Config ──────────────────────────────────────────────────

API_KEY          = os.environ["OPENROUTER_API_KEY"]
BASE_URL         = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
REDIS_URL        = os.getenv("REDIS_URL", "redis://redis:6379/0")
MEMORY_URL       = os.getenv("MEMORY_SERVICE_URL", "http://memory-svc:8081")

MODEL_VISION     = os.getenv("MODEL_VISION",   "qwen/qwen3-vl")
MODEL_COMPLEX    = os.getenv("MODEL_COMPLEX",  "qwen/qwen3-vl")
MODEL_FAST       = os.getenv("MODEL_FAST",     "deepseek/deepseek-v3.2")
MODEL_FALLBACK   = os.getenv("MODEL_FALLBACK", "google/gemini-3.1-flash-lite-preview")

COMPLEXITY_THRESHOLD = int(os.getenv("COMPLEXITY_THRESHOLD", "150"))
CACHE_TTL            = int(os.getenv("CACHE_TTL", "3600"))
RATE_LIMIT_RPM       = int(os.getenv("RATE_LIMIT_RPM", "60"))
HTTP_REFERER         = os.getenv("HTTP_REFERER", "http://localhost:8080")
X_TITLE              = os.getenv("X_TITLE", "OpenRouter AI Stack")

COMPLEX_KEYWORDS = set(
    os.getenv(
        "COMPLEX_KEYWORDS",
        "architecture,refactor,analyze,explain,debug,review,optimize,design,"
        "implement,algorithm,performance,security,database,system,framework",
    ).split(",")
)

# ─── App ─────────────────────────────────────────────────────

app = FastAPI(title="OpenRouter Smart Router", version="1.0.0")
app.include_router(cost_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Redis client (lazy init) ────────────────────────────────

_redis: Optional[aioredis.Redis] = None

async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis

# ─── Pydantic models ─────────────────────────────────────────

class ContentPart(BaseModel):
    type: str
    text: Optional[str] = None
    image_url: Optional[dict] = None

class Message(BaseModel):
    role: str
    content: Any  # str | list[ContentPart]

class ChatRequest(BaseModel):
    messages: list[Message]
    model: Optional[str] = None           # override auto-routing
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    use_memory: bool = True               # inject relevant memories
    metadata: Optional[dict] = None

# ─── Routing Logic ───────────────────────────────────────────

def _has_image(messages: list[Message]) -> bool:
    """Return True if any message contains an image_url part."""
    for msg in messages:
        if isinstance(msg.content, list):
            for part in msg.content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
                if isinstance(part, ContentPart) and part.type == "image_url":
                    return True
    return False


def _extract_text(messages: list[Message]) -> str:
    """Extract all text content from messages for analysis."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg.content, str):
            parts.append(msg.content)
        elif isinstance(msg.content, list):
            for p in msg.content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif isinstance(p, ContentPart) and p.type == "text" and p.text:
                    parts.append(p.text)
    return " ".join(parts)


def _is_complex(text: str) -> bool:
    """
    Heuristic complexity check:
      1. Total word count ≥ COMPLEXITY_THRESHOLD
      2. Or contains known complex-task keywords
    """
    words = text.split()
    if len(words) >= COMPLEXITY_THRESHOLD:
        return True
    text_lower = text.lower()
    return any(kw in text_lower for kw in COMPLEX_KEYWORDS)


KNOWN_MODELS = {MODEL_VISION, MODEL_COMPLEX, MODEL_FAST, MODEL_FALLBACK}

def detect_feature(request: ChatRequest, model: str) -> str:
    """Map routing decision to a feature label for cost tracking."""
    if _has_image(request.messages):
        return "vision"
    text = _extract_text(request.messages)
    if _is_complex(text):
        return "complex"
    if model == MODEL_FALLBACK:
        return "fallback"
    return "standard"


def select_model(request: ChatRequest) -> str:
    """
    Routing decision tree:
      image?   → MODEL_VISION
      complex? → MODEL_COMPLEX
      else     → MODEL_FAST
    Manual override only for exact known model IDs.
    Generic names (auto, gpt-4, claude-*, deepseek-*, ...) trigger auto-routing.
    """
    if request.model and request.model in KNOWN_MODELS:
        log.info("Route: MANUAL → %s", request.model)
        return request.model

    if _has_image(request.messages):
        log.info("Route: VISION → %s", MODEL_VISION)
        return MODEL_VISION

    text = _extract_text(request.messages)
    if _is_complex(text):
        log.info("Route: COMPLEX → %s (words=%d)", MODEL_COMPLEX, len(text.split()))
        return MODEL_COMPLEX

    log.info("Route: FAST → %s", MODEL_FAST)
    return MODEL_FAST


# ─── Cache ───────────────────────────────────────────────────

def _cache_key(model: str, messages: list[Message]) -> str:
    payload = json.dumps(
        {"model": model, "messages": [m.model_dump() for m in messages]},
        sort_keys=True,
    )
    return "cache:" + hashlib.sha256(payload.encode()).hexdigest()


async def get_cached(key: str, redis: aioredis.Redis) -> Optional[dict]:
    raw = await redis.get(key)
    return json.loads(raw) if raw else None


async def set_cached(key: str, value: dict, redis: aioredis.Redis) -> None:
    await redis.setex(key, CACHE_TTL, json.dumps(value))

# ─── Rate Limiting ───────────────────────────────────────────

async def check_rate_limit(client_ip: str, redis: aioredis.Redis) -> None:
    key = f"rl:{client_ip}:{int(time.time() // 60)}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count > RATE_LIMIT_RPM:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

# ─── Memory Injection ────────────────────────────────────────

async def inject_memories(messages: list[Message], query: str) -> list[Message]:
    """
    Retrieve relevant memories from memory-svc and prepend
    a system context message if any are found.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                f"{MEMORY_URL}/search",
                json={"query": query, "limit": 5},
            )
            if resp.status_code == 200:
                memories = resp.json().get("memories", [])
                if memories:
                    ctx = "Relevant context from previous sessions:\n" + "\n".join(
                        f"- Q: {m['query']}\n  A: {m['response']}" for m in memories
                    )
                    system_msg = Message(role="system", content=ctx)
                    # Prepend after existing system messages
                    sys_msgs   = [m for m in messages if m.role == "system"]
                    other_msgs = [m for m in messages if m.role != "system"]
                    return sys_msgs + [system_msg] + other_msgs
    except Exception as e:
        log.warning("Memory injection failed (non-fatal): %s", e)
    return messages

# ─── OpenRouter Call ─────────────────────────────────────────

def _or_headers() -> dict:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "HTTP-Referer":  HTTP_REFERER,
        "X-Title":       X_TITLE,
        "Content-Type":  "application/json",
    }


def _build_payload(
    model: str,
    messages: list[Message],
    temperature: Optional[float],
    max_tokens: Optional[int],
    stream: bool,
) -> dict:
    payload: dict = {
        "model":    model,
        "messages": [m.model_dump(exclude_none=True) for m in messages],
        "stream":   stream,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


async def call_openrouter(
    model: str,
    messages: list[Message],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> dict:
    """Non-streaming call. Returns parsed JSON dict."""
    payload = _build_payload(model, messages, temperature, max_tokens, stream=False)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers=_or_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def stream_openrouter(
    model: str,
    messages: list[Message],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> AsyncGenerator[bytes, None]:
    """
    Streaming call — keeps the httpx client alive for the full stream.
    Client is only closed after the last byte is yielded.
    """
    payload = _build_payload(model, messages, temperature, max_tokens, stream=True)
    # Client must live for the entire generator lifetime — do NOT use a helper
    # function that returns the response, as the context manager would close it.
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/chat/completions",
            headers=_or_headers(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk


async def call_with_fallback(
    model: str,
    messages: list[Message],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> tuple[dict, str]:
    """Non-streaming with fallback. Returns (response_dict, model_used)."""
    for attempt_model in [model, MODEL_FALLBACK]:
        try:
            result = await call_openrouter(attempt_model, messages, temperature, max_tokens)
            if attempt_model != model:
                log.warning("Fallback used: %s → %s", model, attempt_model)
            return result, attempt_model
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 503, 502) and attempt_model != MODEL_FALLBACK:
                log.warning("Model %s returned %d, trying fallback", model, e.response.status_code)
                continue
            raise
    raise HTTPException(status_code=503, detail="All models unavailable")


async def stream_with_fallback(
    model: str,
    messages: list[Message],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> AsyncGenerator[bytes, None]:
    """
    Streaming with fallback. Tries MODEL_FALLBACK if first model fails on first chunk.
    Yields raw SSE bytes; client lifecycle is fully contained inside.
    """
    for attempt_model in [model, MODEL_FALLBACK]:
        try:
            first_chunk = True
            async for chunk in stream_openrouter(attempt_model, messages, temperature, max_tokens):
                first_chunk = False
                yield chunk
            return  # stream completed successfully
        except httpx.HTTPStatusError as e:
            if first_chunk and e.response.status_code in (429, 503, 502) and attempt_model != MODEL_FALLBACK:
                log.warning("Stream fallback: %s → %s (HTTP %d)", model, MODEL_FALLBACK, e.response.status_code)
                continue
            raise
        except Exception:
            if attempt_model != MODEL_FALLBACK:
                log.warning("Stream fallback: %s → %s (exception)", model, MODEL_FALLBACK)
                continue
            raise
    raise HTTPException(status_code=503, detail="All models unavailable")

# ─── Background Memory Store ─────────────────────────────────

async def store_interaction(messages: list[Message], response_text: str) -> None:
    """Silently store the interaction in memory service."""
    try:
        # Extract last user message as the query
        user_msgs = [m for m in messages if m.role == "user"]
        if not user_msgs:
            return
        last_user = _extract_text([user_msgs[-1]])
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{MEMORY_URL}/store",
                json={
                    "query":    last_user[:500],
                    "response": response_text[:1000],
                    "metadata": {"timestamp": time.time()},
                },
            )
    except Exception as e:
        log.debug("Memory store failed (non-fatal): %s", e)

# ─── Stream Helper ───────────────────────────────────────────

async def _stream_and_remember(
    model: str,
    messages: list[Message],
    temperature: Optional[float],
    max_tokens: Optional[int],
    feature: str = "standard",
) -> AsyncGenerator[bytes, None]:
    """
    Proper streaming generator:
    - httpx client lifetime is fully enclosed here (no premature close)
    - collects response text for background memory storage
    - extracts usage from final SSE chunk for cost tracking
    """
    full_response: list[str] = []
    t_start = time.monotonic()
    usage: dict = {}

    async for chunk in stream_with_fallback(model, messages, temperature, max_tokens):
        yield chunk
        for line in chunk.decode(errors="ignore").split("\n"):
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                parsed = json.loads(line[6:])
                delta = parsed["choices"][0].get("delta", {}).get("content", "")
                if delta:
                    full_response.append(delta)
                if "usage" in parsed:
                    usage = parsed["usage"]
            except Exception:
                pass

    latency_ms = int((time.monotonic() - t_start) * 1000)
    asyncio.create_task(store_interaction(messages, "".join(full_response)))

    if usage:
        asyncio.create_task(asyncio.to_thread(
            store_cost,
            model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            usage.get("cost", 0.0),
            feature,
            None,
            latency_ms,
        ))

# ─── Routes ──────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-router"}


@app.get("/models")
@app.get("/v1/models")
async def list_models():
    """Return available models in OpenAI-compatible format."""
    models = [
        {"id": MODEL_VISION,   "object": "model", "owned_by": "openrouter", "role": "vision+complex"},
        {"id": MODEL_FAST,     "object": "model", "owned_by": "openrouter", "role": "fast"},
        {"id": MODEL_FALLBACK, "object": "model", "owned_by": "openrouter", "role": "fallback"},
    ]
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatRequest):
    redis = await get_redis()

    # Rate limit by client IP
    client_ip = request.client.host if request.client else "unknown"
    await check_rate_limit(client_ip, redis)

    # Check for special dispatchers first
    user_text = _extract_text(body.messages)

    if detect_research_trigger(user_text):
        log.info("Dispatch: RESEARCH → %s", os.getenv("MODEL_SEARCH", "perplexity/sonar"))
        msgs = [m.model_dump() for m in body.messages]
        return StreamingResponse(
            research_dispatcher.handle(msgs, body.stream, body.temperature, body.max_tokens),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "X-Model-Routed": "research"},
        )

    # Select model via routing logic
    model = select_model(body)
    feature = detect_feature(body, model)

    # Inject memories from previous sessions
    messages = body.messages
    if body.use_memory:
        query = _extract_text(messages)
        messages = await inject_memories(messages, query)

    # Cache lookup (skip for streaming)
    if not body.stream:
        cache_key = _cache_key(model, messages)
        cached = await get_cached(cache_key, redis)
        if cached:
            log.debug("Cache hit for model=%s", model)
            cached["_cached"] = True
            return JSONResponse(cached)

    # ── Streaming path ───────────────────────────────────────
    if body.stream:
        return StreamingResponse(
            _stream_and_remember(model, messages, body.temperature, body.max_tokens, feature),
            media_type="text/event-stream",
            headers={
                "Cache-Control":    "no-cache",
                "X-Accel-Buffering": "no",
                "X-Model-Routed":   model,
            },
        )

    # ── Non-streaming path ───────────────────────────────────
    result, used_model = await call_with_fallback(
        model, messages, body.temperature, body.max_tokens
    )

    # Inject routing metadata
    result["_routing"] = {"requested": model, "used": used_model}

    # Cache + store memory + track cost async
    asyncio.create_task(set_cached(cache_key, result, redis))
    response_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    asyncio.create_task(store_interaction(messages, response_text))

    usage = result.get("usage", {})
    if usage:
        asyncio.create_task(asyncio.to_thread(
            store_cost,
            used_model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            usage.get("cost", 0.0),
            feature,
        ))

    return JSONResponse(result)


@app.post("/route-info")
async def route_info(body: ChatRequest):
    """Dry-run: show which model would be selected without calling it."""
    model = select_model(body)
    text  = _extract_text(body.messages)
    return {
        "selected_model":  model,
        "has_image":       _has_image(body.messages),
        "is_complex":      _is_complex(text),
        "word_count":      len(text.split()),
        "threshold":       COMPLEXITY_THRESHOLD,
    }
