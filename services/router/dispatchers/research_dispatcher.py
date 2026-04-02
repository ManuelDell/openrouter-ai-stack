"""
research_dispatcher.py — Web research via Perplexity/Sonar + hybrid URL fetching.

Flow:
  1. Route query to perplexity/sonar (built-in web search, via OpenRouter)
  2. Extract URLs from response citations
  3. Fetch each URL in parallel (text or screenshot)
  4. Summarize visual content with Qwen3-VL
  5. Track cost per sub-type (text / visual)

Single responsibility: orchestrate research. I/O goes through injected helpers.
"""

import asyncio
import base64
import json
import logging
import os
import time
from typing import AsyncGenerator, Optional

import httpx

from utils.content_fetcher import fetch_or_screenshot
from utils.cost_tracker import store_cost
from utils.request_analyzer import extract_urls

log = logging.getLogger("research_dispatcher")

API_KEY      = os.environ["OPENROUTER_API_KEY"]
BASE_URL     = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_SEARCH = os.getenv("MODEL_SEARCH", "perplexity/sonar")
MODEL_VISION = os.getenv("MODEL_VISION", "qwen/qwen3-vl-32b-instruct")
MODEL_FAST   = os.getenv("MODEL_FAST",   "deepseek/deepseek-v3.2")

MAX_URLS     = int(os.getenv("RESEARCH_MAX_URLS", "3"))

_HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type":  "application/json",
    "HTTP-Referer":  os.getenv("HTTP_REFERER", "http://localhost:8085"),
    "X-Title":       os.getenv("X_TITLE", "OpenRouter AI Stack"),
}


async def _call(model: str, messages: list[dict], stream: bool = False) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers=_HEADERS,
            json={"model": model, "messages": messages, "stream": stream},
        )
        resp.raise_for_status()
        return resp.json()


async def _summarize_url(url: str, query: str) -> tuple[str, str]:
    """Fetch URL, summarise with appropriate model. Returns (summary, sub_type)."""
    content, is_visual = await fetch_or_screenshot(url)

    if is_visual:
        messages = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{content}"}},
            {"type": "text", "text": f"Describe the key information in this screenshot relevant to: {query}"},
        ]}]
        result = await _call(MODEL_VISION, messages)
        summary = result["choices"][0]["message"]["content"]
        usage = result.get("usage", {})
        asyncio.get_event_loop().call_soon(
            lambda: asyncio.ensure_future(asyncio.to_thread(
                store_cost, MODEL_VISION,
                usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                usage.get("cost", 0.0), "research", "visual",
            ))
        )
        return f"[{url}]\n{summary}", "visual"
    else:
        messages = [{"role": "user", "content":
            f"Summarize the following content in relation to: '{query}'\n\n{content}"}]
        result = await _call(MODEL_FAST, messages)
        summary = result["choices"][0]["message"]["content"]
        usage = result.get("usage", {})
        asyncio.get_event_loop().call_soon(
            lambda: asyncio.ensure_future(asyncio.to_thread(
                store_cost, MODEL_FAST,
                usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                usage.get("cost", 0.0), "research", "text",
            ))
        )
        return f"[{url}]\n{summary}", "text"


async def handle(
    messages: list[dict],
    stream: bool,
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> AsyncGenerator[bytes, None]:
    """
    Full research pipeline as a streaming generator.
    Yields SSE-formatted chunks compatible with OpenAI streaming format.
    """
    t_start = time.monotonic()

    # Extract the user's query
    user_text = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"
         and isinstance(m.get("content"), str)),
        "",
    )

    def _sse(text: str, finish: bool = False) -> bytes:
        chunk = {
            "id": "research-0",
            "object": "chat.completion.chunk",
            "choices": [{"delta": {"content": text} if not finish else {},
                         "finish_reason": "stop" if finish else None,
                         "index": 0}],
        }
        return f"data: {json.dumps(chunk)}\n\n".encode()

    yield _sse("🔍 Searching the web...\n\n")

    # Step 1: Perplexity/Sonar search
    try:
        search_result = await _call(MODEL_SEARCH, messages)
        search_text = search_result["choices"][0]["message"]["content"]
        usage = search_result.get("usage", {})
        asyncio.ensure_future(asyncio.to_thread(
            store_cost, MODEL_SEARCH,
            usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
            usage.get("cost", 0.0), "research", "search",
        ))
    except Exception as e:
        log.warning("Perplexity search failed: %s", e)
        yield _sse(f"⚠️ Search unavailable: {e}\n\n")
        yield _sse("", finish=True)
        yield b"data: [DONE]\n\n"
        return

    yield _sse(f"{search_text}\n\n")

    # Step 2: Extract + fetch URLs from response or original message
    all_urls = extract_urls(search_text) or extract_urls(user_text)
    urls_to_fetch = all_urls[:MAX_URLS]

    if urls_to_fetch:
        yield _sse(f"\n---\n📄 Fetching {len(urls_to_fetch)} source(s)...\n\n")
        tasks = [_summarize_url(url, user_text) for url in urls_to_fetch]
        summaries = await asyncio.gather(*tasks, return_exceptions=True)

        for url, result in zip(urls_to_fetch, summaries):
            if isinstance(result, Exception):
                log.debug("URL fetch failed for %s: %s", url, result)
                continue
            summary, sub_type = result
            yield _sse(f"\n{summary}\n")

    latency_ms = int((time.monotonic() - t_start) * 1000)
    log.info("Research completed in %dms, urls=%d", latency_ms, len(urls_to_fetch))

    yield _sse("", finish=True)
    yield b"data: [DONE]\n\n"
