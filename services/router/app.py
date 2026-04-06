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
from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from utils.cost_tracker import store_cost
from routes.cost_routes import router as cost_router
from tools.definitions import TOOL_DEFINITIONS
from tools.executor import execute_tool
from models.class_router import ALIASES, CODING_CLASSES, resolve_class_chain, increment_usage, get_class_status

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

WEBUI_INTERNAL_URL = os.getenv("WEBUI_INTERNAL_URL", "http://open-webui:8080")
MASTER_API_KEY     = os.getenv("MASTER_API_KEY", "openrouter-via-proxy")

COMPLEX_KEYWORDS = set(
    os.getenv(
        "COMPLEX_KEYWORDS",
        "architecture,refactor,analyze,explain,debug,review,optimize,design,"
        "implement,algorithm,performance,security,database,system,framework",
    ).split(",")
)

MODEL_AUDIO        = os.getenv("MODEL_AUDIO", "xiaomi/mimo-v2-omni")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
GROQ_WHISPER_URL   = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-large-v3-turbo")

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
    tools: Optional[list] = None          # client-provided tools (e.g. Continue agent)
    reasoning_effort: Optional[str] = None  # "low" | "medium" | "high" → tool iteration budget

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


async def resolve_model(
    request: "ChatRequest", redis, need_tools: bool = False
) -> tuple[str, list[str]]:
    """
    Returns (primary_model, fallback_chain).
    For class aliases: full tier chain (free → paid), no global fallback mixed in.
    For heuristic routing: empty chain (stream_with_fallback adds MODEL_FALLBACK).
    """
    if request.model:
        class_name = ALIASES.get(request.model.lower())
        if class_name:
            chain = await resolve_class_chain(class_name, redis, need_tools)
            log.info("Route: CLASS %s → chain=%s", class_name, chain)
            return chain[0], chain[1:]
    return select_model(request), []


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

# ─── User Identity ───────────────────────────────────────────

async def _resolve_user_id(
    api_key: str,
    headers: dict,
    redis: aioredis.Redis,
) -> str:
    """
    Resolve user identity from the request.

    Priority:
    1. Master key + OWT-forwarded header → trust OWT's user email directly.
    2. Non-master key → validate against OWT's API (cached 5 min in Redis).
    3. Fallback → "default".

    Security: Forwarded OWT headers are only trusted with the master key because
    only OWT (which already authenticated the user) sends that key+header combo.
    A direct caller with a non-master key must prove identity via OWT API validation.
    """
    if api_key == MASTER_API_KEY:
        email = headers.get("x-openwebui-user-email")
        if email:
            return email
        return "default"

    if not api_key:
        return "default"

    key_hash  = hashlib.sha256(api_key.encode()).hexdigest()
    cache_key = f"user_key:{key_hash}"
    cached    = await redis.get(cache_key)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                f"{WEBUI_INTERNAL_URL}/api/v1/users/user/info",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as je:
                    log.warning("OWT user/info: 200 OK but JSON parse failed: %s | body: %s", je, resp.text[:200])
                    return "default"
                if not isinstance(data, dict):
                    log.warning("OWT user/info: expected dict, got %s | body: %s", type(data).__name__, resp.text[:200])
                    return "default"
                log.info("OWT user/info response keys: %s | data: %s", list(data.keys()), str(data)[:200])
                user_id = data.get("email") or data.get("id") or "default"
                await redis.setex(cache_key, 300, user_id)
                log.info("Resolved API key → user=%s (cached 5 min)", user_id)
                return user_id
            else:
                log.warning("OWT user/info returned HTTP %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("OWT key validation failed: %s", e)

    return "default"


# ─── Memory Injection ────────────────────────────────────────

async def inject_memories(
    messages: list[Message], query: str, user_id: str = "default"
) -> list[Message]:
    """
    Retrieve relevant memories from memory-svc and prepend
    a system context message if any are found.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                f"{MEMORY_URL}/search",
                json={"query": query, "limit": 5, "user_id": user_id},
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
    messages: list,  # list[Message] or list[dict]
    temperature: Optional[float],
    max_tokens: Optional[int],
    stream: bool,
    tools: Optional[list] = None,
) -> dict:
    serialized = [
        m if isinstance(m, dict) else m.model_dump(exclude_none=True)
        for m in messages
    ]
    payload: dict = {"model": model, "messages": serialized, "stream": stream}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return payload


async def call_openrouter(
    model: str,
    messages: list,
    temperature: Optional[float],
    max_tokens: Optional[int],
    tools: Optional[list] = None,
) -> dict:
    """Non-streaming call. Returns parsed JSON dict."""
    payload = _build_payload(model, messages, temperature, max_tokens, stream=False, tools=tools)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers=_or_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def stream_openrouter(
    model: str,
    messages: list,
    temperature: Optional[float],
    max_tokens: Optional[int],
    tools: Optional[list] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Streaming call — keeps the httpx client alive for the full stream.
    Client is only closed after the last byte is yielded.
    """
    payload = _build_payload(model, messages, temperature, max_tokens, stream=True, tools=tools)
    # Client must live for the entire generator lifetime — do NOT use a helper
    # function that returns the response, as the context manager would close it.
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/chat/completions",
            headers=_or_headers(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                # OpenRouter sometimes sends HTTP 200 but injects a JSON error
                # into the SSE stream (e.g. rate_limit_exceeded as code 429).
                # Detect before yielding so stream_with_fallback can fall back.
                text = chunk.decode(errors="ignore")
                if '"error_type"' in text:
                    code = 500
                    try:
                        for line in text.split("\n"):
                            line = line.strip()
                            if line.startswith("data: "):
                                line = line[6:]
                            if line.startswith("{"):
                                err = json.loads(line)
                                code = err.get("code") or err.get("details", {}).get("code", 500)
                                break
                    except Exception:
                        pass
                    raise RuntimeError(f"OpenRouter in-stream error (code={code})")
                yield chunk


async def call_with_fallback(
    model: str,
    messages: list,
    temperature: Optional[float],
    max_tokens: Optional[int],
    tools: Optional[list] = None,
    extra_fallbacks: Optional[list[str]] = None,
) -> tuple[dict, str]:
    """Non-streaming with fallback. Returns (response_dict, model_used).
    Tries: model → extra_fallbacks (class tiers) → MODEL_FALLBACK (global paid).
    """
    candidates = [model] + (extra_fallbacks or [])
    if MODEL_FALLBACK not in candidates:
        candidates.append(MODEL_FALLBACK)
    for attempt_model in candidates:
        try:
            result = await call_openrouter(attempt_model, messages, temperature, max_tokens, tools)
            if attempt_model != model:
                log.warning("Fallback used: %s → %s", model, attempt_model)
            return result, attempt_model
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 402, 404, 422, 429, 502, 503) and attempt_model != candidates[-1]:
                log.warning("Model %s returned %d, trying next", attempt_model, e.response.status_code)
                continue
            if e.response.status_code == 400 and tools:
                log.warning("Model %s returned 400 with tools — retrying without tools", attempt_model)
                result = await call_openrouter(attempt_model, messages, temperature, max_tokens, tools=None)
                return result, attempt_model
            raise
    raise HTTPException(status_code=503, detail="All models unavailable")


async def stream_with_fallback(
    model: str,
    messages: list,
    temperature: Optional[float],
    max_tokens: Optional[int],
    tools: Optional[list] = None,
    extra_fallbacks: Optional[list[str]] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Streaming with fallback.
    Tries: model → extra_fallbacks (class tiers) → MODEL_FALLBACK (global paid).
    On HTTP 400 with tools: retries same model without tools.
    Yields raw SSE bytes; client lifecycle is fully contained inside.
    """
    candidates = [model] + (extra_fallbacks or [])
    if MODEL_FALLBACK not in candidates:
        candidates.append(MODEL_FALLBACK)
    last = candidates[-1]
    for attempt_model in candidates:
        try:
            first_chunk = True
            async for chunk in stream_openrouter(attempt_model, messages, temperature, max_tokens, tools):
                first_chunk = False
                yield chunk
            return  # stream completed successfully
        except httpx.HTTPStatusError as e:
            if first_chunk and e.response.status_code in (400, 402, 404, 422, 429, 502, 503) and attempt_model != last:
                log.warning("Stream fallback: %s → next (HTTP %d)", attempt_model, e.response.status_code)
                continue
            if first_chunk and e.response.status_code == 400 and tools:
                log.warning("Model %s returned 400 with tools — retrying without tools", attempt_model)
                try:
                    async for chunk in stream_openrouter(attempt_model, messages, temperature, max_tokens, tools=None):
                        yield chunk
                    return
                except httpx.HTTPStatusError as e2:
                    if attempt_model != last:
                        log.warning("Model %s returned %d without tools — falling back", attempt_model, e2.response.status_code)
                        continue
                    raise
            raise
        except RuntimeError as e:
            if attempt_model != last:
                log.warning("Stream fallback: %s → next (%s)", attempt_model, e)
                continue
            raise
        except Exception:
            if attempt_model != last:
                log.warning("Stream fallback: %s → next (exception)", attempt_model)
                continue
            raise
    raise HTTPException(status_code=503, detail="All models unavailable")

# ─── Background Memory Store ─────────────────────────────────

async def store_interaction(
    messages: list[Message], response_text: str, user_id: str = "default"
) -> None:
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
                    "user_id":  user_id,
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
    user_id: str = "default",
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
    asyncio.create_task(store_interaction(messages, "".join(full_response), user_id))

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

# ─── Tool Calling ────────────────────────────────────────────

async def _keepalive_stream(
    gen: AsyncGenerator[bytes, None],
    interval: float = 2.0,
) -> AsyncGenerator[bytes, None]:
    """
    Wrap an async byte generator, injecting SSE keepalive comment lines
    whenever no chunk arrives within `interval` seconds.

    This prevents SSE clients (Continue IDE, browsers) from timing out
    during long LLM think-times or between tool-call iterations.
    """
    queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

    async def _producer() -> None:
        try:
            async for chunk in gen:
                await queue.put(chunk)
        finally:
            await queue.put(None)  # sentinel — generator exhausted

    task = asyncio.create_task(_producer())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
                continue
            if item is None:
                break
            yield item
    finally:
        task.cancel()


def _sse_chunk(text: str) -> bytes:
    """Wrap plain text as an SSE content delta chunk."""
    chunk = {"choices": [{"delta": {"content": text}, "finish_reason": None, "index": 0}]}
    return f"data: {json.dumps(chunk)}\n\n".encode()


async def _safe_stream(gen: AsyncGenerator[bytes, None]) -> AsyncGenerator[bytes, None]:
    """
    Wrap any streaming generator so that unhandled exceptions are surfaced
    as a visible SSE error chunk instead of a silent connection drop.
    """
    try:
        async for chunk in gen:
            yield chunk
    except Exception as e:
        msg = f"\n\n⚠️ **Verbindungsfehler:** {type(e).__name__}: {str(e)[:200]}"
        yield _sse_chunk(msg)
        yield b'data: {"choices":[{"delta":{},"finish_reason":"stop","index":0}]}\n\n'
        yield b"data: [DONE]\n\n"
        log.error("Streaming exception surfaced to user: %s", e, exc_info=True)


async def _compress_context(msgs: list[dict], model: str) -> list[dict]:
    """
    Token-limit reached: ask the model for a structured summary of the work
    so far, then rebuild the message list with only system prompts + summary.
    The original user task is preserved so the model can continue seamlessly.
    """
    original_task = next(
        (m["content"] for m in msgs if m.get("role") == "user"),
        "Aufgabe unbekannt",
    )
    if isinstance(original_task, list):
        original_task = " ".join(
            p.get("text", "") for p in original_task if isinstance(p, dict)
        )
    system_msgs = [m for m in msgs if m.get("role") == "system"]
    summary_prompt = (
        "Fasse die bisherige Arbeit in max. 400 Wörtern zusammen:\n"
        "1. Aufgabe (was war gefragt)\n"
        "2. Durchgeführte Schritte und Recherchen\n"
        "3. Wichtige Erkenntnisse und Ergebnisse\n"
        "4. Was noch offen/unfertig ist\n\n"
        "Schreibe NUR die Zusammenfassung, keine Einleitung."
    )
    try:
        summary_resp, _ = await call_with_fallback(
            model,
            msgs + [{"role": "user", "content": summary_prompt}],
            temperature=0.3,
            max_tokens=600,
        )
        summary = summary_resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("Context compression failed: %s", e)
        summary = "[Zusammenfassung nicht verfügbar]"
    log.info("Context compressed for model=%s", model)
    return system_msgs + [
        {
            "role": "user",
            "content": (
                f"## Bisheriger Fortschritt (Kontext komprimiert)\n{summary}\n\n"
                f"## Originalaufgabe\n{original_task}\n\n"
                "Fahre nun nahtlos mit der Aufgabe fort."
            ),
        }
    ]


def _acc_tool_calls(acc: dict[int, dict], tool_calls_delta: list) -> None:
    """Merge streaming tool-call delta chunks into acc[index]."""
    for tc in tool_calls_delta:
        idx = tc.get("index", 0)
        if idx not in acc:
            acc[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        if tc.get("id"):
            acc[idx]["id"] = tc["id"]
        fn = tc.get("function", {})
        acc[idx]["function"]["name"]      += fn.get("name", "")
        acc[idx]["function"]["arguments"] += fn.get("arguments", "")


async def _tool_loop_non_streaming(
    model: str,
    messages: list[dict],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> tuple[dict, list[dict]]:
    """
    Non-streaming tool-calling loop.
    Calls the LLM with TOOL_DEFINITIONS; if it returns tool_calls, executes them
    and loops. Returns (final_response, updated_message_list).
    """
    msgs = list(messages)
    for _ in range(5):
        result, _ = await call_with_fallback(model, msgs, temperature, max_tokens, TOOL_DEFINITIONS)
        choice = result["choices"][0]
        if choice.get("finish_reason") != "tool_calls":
            return result, msgs
        tool_calls = choice.get("message", {}).get("tool_calls", [])
        if not tool_calls:
            return result, msgs
        msgs.append(choice["message"])
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"]["arguments"])
            except Exception:
                args = {}
            log.info("Tool: %s(%s)", tc["function"]["name"], list(args.keys()))
            tool_result = await execute_tool(tc["function"]["name"], args)
            # Image bypass: wrap URL as markdown so the LLM can reference it
            if tc["function"]["name"] == "generate_image" and tool_result.startswith("http"):
                tool_result = f"![Generiertes Bild]({tool_result})"
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_result})
    return result, msgs


async def _stream_with_tool_loop(
    model: str,
    messages: list[dict],
    temperature: Optional[float],
    max_tokens: Optional[int],
    feature: str,
    user_id: str = "default",
    max_iterations: int = 10,
    effort: Optional[str] = None,
    fallback_chain: Optional[list[str]] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Streaming with tool-calling support.

    Strategy:
    - Buffer SSE chunks until it's clear whether the response is plain text
      or a tool call (tool_calls appear in the first non-trivial delta).
    - Plain text → flush buffer and pass subsequent chunks through directly.
    - Tool calls → accumulate, execute, loop back for the next LLM turn.

    Safety layers:
    - finish_reason=length → _compress_context() + continue (no silent cutoff)
    - Empty text response   → user warning + return (no silent blank)
    - Tool exec exception   → error string fed back to LLM
    - Loop limit reached    → visible ⚠️ message instead of silent [DONE]

    This keeps zero first-byte latency for the common (no-tool) case.
    """
    msgs = list(messages)

    # Ensure the model knows exactly which tools it has and must use them via tool_calls.
    # Without this, reasoning models (e.g. DeepSeek R1) often respond with plain text
    # describing what they would do instead of actually calling the tools.
    _TOOL_HINT = (
        "Du hast folgende Tools die du aktiv per tool_call nutzen MUSST — "
        "beschreibe NICHT was du tun würdest, sondern führe es direkt aus:\n"
        "• web_search(query) — Web-Suche nach aktuellen Informationen\n"
        "• bash(command)     — Shell-Befehle: curl, wget, python, jq, grep, etc.\n"
        "• generate_image(prompt) — KI-Bild generieren\n"
        "• reset_bash()      — Shell-Session zurücksetzen"
    )
    existing_system = [m for m in msgs if m.get("role") == "system"]
    if existing_system:
        last_sys = existing_system[-1]
        last_sys["content"] = last_sys["content"].rstrip() + "\n\n" + _TOOL_HINT
    else:
        msgs.insert(0, {"role": "system", "content": _TOOL_HINT})

    # For deep research (denker default or medium/high effort): inject research strategy.
    # This shifts the model from "answer when it feels like enough" (shallow) to
    # "cover all angles and verify" (Perplexity-style coverage-based stopping).
    if max_iterations >= 15:
        _RESEARCH_HINT = (
            "\n\nFür Recherche-Aufgaben — Strategie:\n"
            "• PLANE: Identifiziere 5–8 Teilfragen die vollständig beantwortet sein müssen\n"
            "• WINKEL: Suche aus verschiedenen Perspektiven (offizielle Quellen, News, Experten, Kritiker)\n"
            "• VERTIEFE: Wenn eine Suche etwas Wichtiges zeigt — suche gezielt mehr dazu\n"
            "• COVERAGE: Stoppe erst wenn alle Teilfragen mit Quellen belegt sind\n"
            "• VERIFIZIERE: Wichtige Fakten aus mindestens 2 unabhängigen Quellen bestätigen"
        )
        sys_msg = next(m for m in msgs if m.get("role") == "system")
        sys_msg["content"] = sys_msg["content"].rstrip() + _RESEARCH_HINT

    for iteration in range(max_iterations):
        pre_buffer: list[bytes] = []   # chunks before we know text vs. tool-call
        tool_acc: dict[int, dict] = {} # accumulated tool call fragments
        decided          = False       # True once text or tool_calls detected
        is_tool          = False
        in_think_block   = False       # True while streaming <think> reasoning (QwQ/R1)
        asst_text        = ""          # assistant content during tool-call response
        full_text: list[str] = []      # collected for memory storage
        usage: dict      = {}
        finish_reason_seen: Optional[str] = None

        async for raw_chunk in _keepalive_stream(
            stream_with_fallback(model, msgs, temperature, max_tokens, TOOL_DEFINITIONS, fallback_chain)
        ):
            for line in raw_chunk.decode(errors="ignore").split("\n"):
                if not line.startswith("data: "):
                    continue
                if line == "data: [DONE]":
                    if not is_tool:
                        yield b"data: [DONE]\n\n"
                    break

                try:
                    parsed = json.loads(line[6:])
                except Exception:
                    if decided and not is_tool:
                        yield (line + "\n\n").encode()
                    else:
                        pre_buffer.append((line + "\n\n").encode())
                    continue

                choice = parsed.get("choices", [{}])[0]
                delta  = choice.get("delta", {})
                if "usage" in parsed:
                    usage = parsed["usage"]

                # Track finish_reason for safety checks below
                fr = choice.get("finish_reason")
                if fr:
                    finish_reason_seen = fr

                if not decided:
                    if "tool_calls" in delta:
                        # Flush buffered setup chunks (pre_buffer cleared by reasoning branch already)
                        for b in pre_buffer:
                            yield b
                        pre_buffer.clear()
                        in_think_block = False
                        decided = True
                        is_tool = True
                        _acc_tool_calls(tool_acc, delta["tool_calls"])
                    elif delta.get("content"):
                        in_think_block = False
                        decided = True
                        is_tool = False
                        for b in pre_buffer:
                            yield b
                        pre_buffer.clear()
                        yield (line + "\n\n").encode()
                        full_text.append(delta["content"])
                    elif delta.get("reasoning"):
                        # Reasoning model (QwQ/R1 via reasoning field) — pass raw chunks
                        # through immediately to keep OWT connection alive. OWT renders
                        # the reasoning field natively as collapsible <details> blocks.
                        # Without this, 100+ reasoning-only chunks are pre-buffered and
                        # OWT times out with an empty response.
                        if not in_think_block:
                            for b in pre_buffer:  # flush role-setup chunks first
                                yield b
                            pre_buffer.clear()
                            in_think_block = True
                        yield (line + "\n\n").encode()
                    else:
                        pre_buffer.append((line + "\n\n").encode())
                elif is_tool:
                    if "tool_calls" in delta:
                        _acc_tool_calls(tool_acc, delta["tool_calls"])
                    if delta.get("content"):
                        asst_text += delta["content"]
                else:
                    # Text mode — but model may still switch to tool_calls after initial text
                    if "tool_calls" in delta:
                        is_tool = True
                        _acc_tool_calls(tool_acc, delta["tool_calls"])
                        # Don't yield tool_call chunks to OWT — handle internally
                    else:
                        yield (line + "\n\n").encode()
                        if delta.get("content"):
                            full_text.append(delta["content"])

        # ── Safety checks after stream ends ──────────────────────────────
        if not is_tool:
            # Token-limit: compress context and retry rather than cutting off
            if finish_reason_seen == "length":
                yield _sse_chunk(
                    "\n\n📝 *Kontext-Limit erreicht — erstelle Zusammenfassung und fahre fort...*\n"
                )
                msgs = await _compress_context(msgs, model)
                continue  # next iteration with compressed context

            # Empty response: warn user (stream_with_fallback will try next model)
            if not "".join(full_text).strip():
                yield _sse_chunk(
                    "\n\n⚠️ **Modell hat keine Antwort geliefert** — bitte erneut versuchen "
                    "oder Aufgabe umformulieren.\n"
                )
                yield b'data: {"choices":[{"delta":{},"finish_reason":"stop","index":0}]}\n\n'
                yield b"data: [DONE]\n\n"
                return

            # Normal text response — fire-and-forget memory + cost + usage
            asyncio.create_task(store_interaction(msgs, "".join(full_text), user_id))
            asyncio.create_task(increment_usage(model, _redis))
            if usage:
                asyncio.create_task(asyncio.to_thread(
                    store_cost, model,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("cost", 0.0),
                    feature, None, 0,
                ))
            return

        # ── Execute tool calls with <think> progress display ──────
        tool_calls = [tool_acc[i] for i in sorted(tool_acc)]
        msgs.append({"role": "assistant", "content": asst_text or None, "tool_calls": tool_calls})

        yield _sse_chunk("<think>\n")

        tool_results: dict[str, str] = {}
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except Exception:
                args = {}

            # ── Show what we're doing ─────────────────────────
            if name == "web_search":
                query = args.get("query", "")
                yield _sse_chunk(f'🔍 Suche: "{query}"\n')
            elif name == "bash":
                cmd = args.get("command", "").replace("\n", " ").strip()[:120]
                yield _sse_chunk(f'💻 $ {cmd}\n')
            elif name == "generate_image":
                prompt = args.get("prompt", "")[:80]
                yield _sse_chunk(f'🖼️ Generiere Bild: "{prompt}"\n')
            elif name == "reset_bash":
                yield _sse_chunk("🔄 Bash-Session zurücksetzen\n")

            log.info("Tool: %s(%s)", name, list(args.keys()))
            # Run tool while sending keepalive pings every 2s so clients
            # (Continue, OWT) don't time out during slow tool execution
            exec_task = asyncio.create_task(execute_tool(name, args))
            while not exec_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(exec_task), timeout=2.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
            try:
                result = exec_task.result()
            except Exception as exc:
                result = f"[Tool-Fehler] {type(exc).__name__}: {exc}"
                log.error("Tool execution raised for %s: %s", name, exc, exc_info=True)
            tool_results[tc["id"]] = result

            # ── Show result summary ───────────────────────────
            if name == "web_search":
                n_src = result.count("Source: ")
                yield _sse_chunk(f"→ {n_src} Quellen gelesen\n\n")
            elif name == "bash":
                first = result.splitlines()[0][:100] if result.strip() else "(kein Output)"
                yield _sse_chunk(f"→ {first}\n\n")
            elif name == "generate_image":
                if result.startswith("http"):
                    yield _sse_chunk("→ Bild generiert ✓\n\n")
                else:
                    yield _sse_chunk(f"→ Fehler: {result[:80]}\n\n")
            elif name == "reset_bash":
                yield _sse_chunk("→ Session zurückgesetzt ✓\n\n")

            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        yield _sse_chunk("</think>\n\n")

        # ── Proactive context compression ─────────────────────────────────
        # A 500 in-stream crash from QwQ (context overflow) is NOT caught by
        # finish_reason=length, so compress proactively before the context
        # explodes. Threshold ~80k chars ≈ 20k tokens — safe for all models.
        total_chars = sum(len(str(m.get("content") or "")) for m in msgs)
        if total_chars > 80_000:
            yield _sse_chunk(
                "\n\n📝 *Kontext wächst — komprimiere Gesprächsverlauf...*\n\n"
            )
            msgs = await _compress_context(msgs, model)

        # ── High-effort self-check every 10 iterations ────────────────────
        if effort == "high" and iteration > 0 and (iteration + 1) % 10 == 0:
            yield _sse_chunk(
                f"\n🔍 *Fortschrittscheck nach {iteration + 1} Runden — "
                "überprüfe ob bereits eine sinnvolle Antwort möglich ist...*\n\n"
            )
            msgs.append({
                "role": "user",
                "content": (
                    "[RECHERCHE-CHECK] Welche der ursprünglichen Schlüsselaspekte "
                    "sind NOCH NICHT mit konkreten Quellen belegt? "
                    "Liste sie explizit auf. "
                    "Wenn Lücken existieren: suche gezielt danach — antworte NICHT zuerst. "
                    "Antworte nur wenn ALLE wichtigen Aspekte durch Quellen abgedeckt sind."
                ),
            })

        # ── Image bypass: stream markdown directly, skip second LLM call ──
        only_image = (
            len(tool_calls) == 1
            and tool_calls[0]["function"]["name"] == "generate_image"
        )
        if only_image:
            url = tool_results[tool_calls[0]["id"]]
            if url.startswith("http"):
                yield _sse_chunk(f"Das generierte Bild:\n\n![Generiertes Bild]({url})\n")
            else:
                yield _sse_chunk(url)  # error message
            # Send finish_reason: stop so OWT flushes and renders the last chunk
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop","index":0}]}\n\n'
            yield b"data: [DONE]\n\n"
            return

    # Loop exhausted — force a final answer with gathered information (no error message)
    yield _sse_chunk(
        f"\n\n📋 *{max_iterations} Recherche-Runden ausgeschöpft — "
        "erstelle Antwort mit den gesammelten Informationen...*\n\n"
    )
    msgs.append({
        "role": "user",
        "content": (
            "Du hast alle verfügbaren Recherche-Runden verwendet. "
            "Fasse jetzt alle gesammelten Informationen zu einer vollständigen Antwort zusammen. "
            "Wenn Informationen fehlen, benenne das klar — aber gib auf jeden Fall eine "
            "strukturierte Antwort mit dem was du weißt."
        ),
    })
    async for chunk in _safe_stream(
        stream_with_fallback(model, msgs, temperature, max_tokens, tools=None, extra_fallbacks=fallback_chain)
    ):
        yield chunk


# ─── Routes ──────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-router"}


@app.get("/models")
@app.get("/v1/models")
async def list_models():
    """
    Return available models in OpenAI-compatible format.

    OWT Dropdown (4 entries):
      - auto      → existing heuristic (keyword + complexity routing)
      - denker    → DeepSeek R1 / reasoning, chat
      - allrounder → Llama 3.1 8B / general chat
      - begleiter → Mistral 7B / simple companion

    Coding classes (titan, professional, flitzer) are intentionally omitted —
    they are only reachable via direct alias input in Cline / the IDE.
    """
    models = [
        {"id": "auto",         "object": "model", "owned_by": "router", "role": "auto-routing"},
        {"id": "code-auto",    "object": "model", "owned_by": "router", "role": "coding-tools"},
        {"id": "denker",       "object": "model", "owned_by": "router", "role": "chat-denker"},
        {"id": "allrounder",   "object": "model", "owned_by": "router", "role": "chat-allrounder"},
        {"id": "begleiter",    "object": "model", "owned_by": "router", "role": "chat-begleiter"},
    ]
    return {"object": "list", "data": models}


@app.get("/v1/models/status")
async def model_status():
    """Return current tier usage for all 6 model classes."""
    redis = await get_redis()
    return await get_class_status(redis)


@app.post("/v1/completions")
async def legacy_completions(request: Request):
    """
    Legacy completions endpoint (FIM / tab-autocomplete).
    Continue uses this for code autocomplete. Proxied directly to OpenRouter
    using MODEL_FAST — no routing needed for short autocomplete snippets.
    """
    body = await request.json()
    body.setdefault("model", MODEL_FAST)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/completions",
            headers=_or_headers(),
            json=body,
        )
        if resp.status_code == 404:
            # OpenRouter may not support FIM for this model — fall back to empty
            return JSONResponse({"choices": [{"text": "", "finish_reason": "stop"}]})
        resp.raise_for_status()
    return JSONResponse(resp.json())


@app.post("/v1/images/generations")
async def image_generations(request: Request):
    """
    OpenAI-compatible image generation endpoint for OWT's native image gen.
    Returns b64_json so OWT can store the image itself (no mixed-content issues).
    """
    TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
    if not TOGETHER_API_KEY:
        raise HTTPException(status_code=503, detail="Image generation not configured (TOGETHER_API_KEY missing)")

    body = await request.json()
    prompt = body.get("prompt", "")
    n      = body.get("n", 1)
    size   = body.get("size", "1024x1024")
    width, height = (int(x) for x in size.split("x")) if "x" in size else (1024, 1024)
    model  = os.getenv("MODEL_IMAGEGEN", "black-forest-labs/FLUX.1-schnell")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.together.xyz/v1/images/generations",
            headers={"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "prompt": prompt, "n": n,
                  "width": width, "height": height, "response_format": "b64_json"},
        )
        resp.raise_for_status()
        data = resp.json()

    log.info("ImageGen (native): prompt='%s...' → %d image(s)", prompt[:60], len(data.get("data", [])))
    return JSONResponse({"created": int(time.time()), "data": data.get("data", [])})


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatRequest):
    redis = await get_redis()

    client_ip = request.client.host if request.client else "unknown"
    await check_rate_limit(client_ip, redis)

    # Coding classes (titan/professional/flitzer) are Cline-only:
    # skip router tool injection and require no tool-capable tier.
    resolved_class = ALIASES.get((body.model or "").lower())
    is_coding = resolved_class in CODING_CLASSES

    # need_tools: only inject router tools for non-coding, non-client-tools requests
    client_tools_present = bool(body.tools)
    need_tools = not client_tools_present and not is_coding
    model, fallback_chain = await resolve_model(body, redis, need_tools)
    feature = detect_feature(body, model)

    # ── Resolve user identity ────────────────────────────────
    raw_key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    user_id = await _resolve_user_id(raw_key, dict(request.headers), redis)

    messages = body.messages
    if body.use_memory:
        messages = await inject_memories(messages, _extract_text(messages), user_id)

    msgs = [m.model_dump(exclude_none=True) for m in messages]

    client_tools = body.tools or None
    # Direct proxy: coding classes (Cline) or client-provided tools skip the
    # router's own tool loop entirely — cleaner, no injection, Cline-compatible.
    direct_proxy = is_coding or bool(client_tools)
    log.info("Request: model=%s class=%s user=%s stream=%s direct=%s",
             body.model, resolved_class or "auto", user_id, body.stream, direct_proxy)

    # Tool-loop iteration limit: driven by reasoning_effort when set, else class-based defaults.
    # reasoning_effort is a native OWT parameter (Chat Controls → Advanced Parameters).
    _EFFORT_ITERATIONS: dict[str, int] = {"low": 6, "medium": 15, "high": 50}
    _SMALL_CLASSES = {"begleiter", "flitzer"}
    if body.reasoning_effort in _EFFORT_ITERATIONS:
        max_iter = _EFFORT_ITERATIONS[body.reasoning_effort]
    elif resolved_class == "denker":
        max_iter = 50   # denker default: deep research, no artificial cap
    elif resolved_class in _SMALL_CLASSES:
        max_iter = 5
    else:
        max_iter = 10
    log.info("Tool iterations: max=%d (effort=%s class=%s)", max_iter, body.reasoning_effort, resolved_class)

    # ── Streaming path ───────────────────────────────────────
    if body.stream:
        if direct_proxy:
            return StreamingResponse(
                _safe_stream(_keepalive_stream(
                    stream_with_fallback(model, msgs, body.temperature, body.max_tokens,
                                         client_tools, fallback_chain)
                )),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                         "X-Model-Routed": model,
                         "X-Tool-Mode": "client" if client_tools else "direct"},
            )
        return StreamingResponse(
            _safe_stream(_stream_with_tool_loop(
                model, msgs, body.temperature, body.max_tokens, feature, user_id,
                max_iter, body.reasoning_effort, fallback_chain
            )),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "X-Model-Routed": model},
        )

    # ── Non-streaming path ───────────────────────────────────
    cache_key = _cache_key(model, messages)
    cached = await get_cached(cache_key, redis)
    if cached:
        log.debug("Cache hit for model=%s", model)
        cached["_cached"] = True
        return JSONResponse(cached)

    if direct_proxy:
        result, _ = await call_with_fallback(model, msgs, body.temperature, body.max_tokens,
                                              client_tools, fallback_chain)
    else:
        result, _ = await _tool_loop_non_streaming(model, msgs, body.temperature, body.max_tokens)
    result["_routing"] = {"model": model}

    response_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    asyncio.create_task(set_cached(cache_key, result, redis))
    asyncio.create_task(store_interaction(messages, response_text, user_id))
    asyncio.create_task(increment_usage(model, redis))

    usage = result.get("usage", {})
    if usage:
        asyncio.create_task(asyncio.to_thread(
            store_cost, model,
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


@app.get("/v1/fetch")
async def fetch_page(url: str, mode: str = "text"):
    """
    Fetch page content for MCP tools.
    mode=text       → extract readable text from HTML
    mode=screenshot → screenshot (base64 JPEG) if Puppeteer available, else text
    """
    from utils.content_fetcher import fetch_or_screenshot, fetch_text
    if mode == "screenshot":
        content, is_visual = await fetch_or_screenshot(url)
    else:
        content = await fetch_text(url) or f"[Could not fetch {url}]"
        is_visual = False
    return {"url": url, "content": content, "is_visual": is_visual}


def _audio_mime(filename: str, content_type: Optional[str]) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
        "ogg": "audio/ogg", "webm": "audio/webm", "flac": "audio/flac",
    }.get(ext, content_type or "audio/mpeg")


async def _transcribe_via_openrouter(audio_bytes: bytes, filename: str, language: Optional[str]) -> str:
    """Send audio as base64 to MiMo-V2-Omni via OpenRouter chat completions."""
    audio_b64 = base64.b64encode(audio_bytes).decode()
    fmt = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"

    prompt = "Transcribe this audio exactly. Return only the transcription text, nothing else."
    if language:
        prompt += f" The audio is in {language}."

    payload = {
        "model": MODEL_AUDIO,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": audio_b64, "format": fmt}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers=_or_headers(),
            json=payload,
        )
        resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


async def _transcribe_via_groq(audio_bytes: bytes, filename: str, language: Optional[str]) -> str:
    """Fallback: Groq Whisper API (requires GROQ_API_KEY)."""
    data: dict = {"model": GROQ_WHISPER_MODEL, "response_format": "text"}
    if language:
        data["language"] = language
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            GROQ_WHISPER_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": (filename, audio_bytes, _audio_mime(filename, None))},
            data=data,
        )
        resp.raise_for_status()
    return resp.text.strip()


@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(
    file: UploadFile = File(...),
    model: str = Form(default="whisper-1"),
    language: Optional[str] = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float = Form(default=0.0),
):
    """
    OpenAI-compatible audio transcription.
    Primary: MiMo-V2-Omni via OpenRouter (no extra key needed).
    Fallback: Groq Whisper (if GROQ_API_KEY is set).
    """
    audio_bytes = await file.read()
    filename = file.filename or "audio.wav"
    transcript = ""
    model_used = MODEL_AUDIO

    # Primary: MiMo-V2-Omni via OpenRouter
    try:
        transcript = await _transcribe_via_openrouter(audio_bytes, filename, language)
        log.info("Transcribed '%s' via %s (%d chars)", filename, MODEL_AUDIO, len(transcript))
    except Exception as e:
        log.warning("MiMo-V2-Omni transcription failed: %s — trying Groq fallback", e)
        # Fallback: Groq Whisper
        if GROQ_API_KEY:
            try:
                transcript = await _transcribe_via_groq(audio_bytes, filename, language)
                model_used = f"groq/{GROQ_WHISPER_MODEL}"
                log.info("Transcribed '%s' via Groq (%d chars)", filename, len(transcript))
            except Exception as e2:
                log.error("Groq fallback also failed: %s", e2)
                raise HTTPException(status_code=503, detail=f"Transcription failed: {e2}")
        else:
            raise HTTPException(status_code=503, detail=f"Transcription failed: {e}")

    # Store transcript in memory (background)
    async def _mem_store(t=transcript, fn=filename):
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                await c.post(
                    f"{MEMORY_URL}/store",
                    json={"query": f"[audio] {fn}",
                          "response": t[:2000],
                          "metadata": {"type": "audio_transcript"}},
                )
        except Exception as ex:
            log.debug("Memory store for audio failed: %s", ex)

    asyncio.create_task(_mem_store())
    asyncio.create_task(asyncio.to_thread(
        store_cost, model_used, 0, 0, 0.0, "audio", "transcription", 0
    ))

    if response_format == "text":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(transcript)

    return JSONResponse({"text": transcript})
