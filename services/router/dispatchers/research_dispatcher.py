"""
research_dispatcher.py — Web research: DuckDuckGo → scrape → synthesize.

No external API keys required beyond the existing OpenRouter key.

Flow:
  1. DuckDuckGo text search (free, no API key) → list of URLs + snippets
  2. Fetch each URL in parallel via httpx + BeautifulSoup
     Visual sites → Chromium screenshot → Qwen3-VL analysis
  3. DeepSeek synthesizes a proper answer from the scraped content
  4. Cost tracked for vision + synthesis steps only (DDG is free)
"""

import asyncio
import json
import logging
import os
import time
from typing import AsyncGenerator, Optional

import httpx
from duckduckgo_search import DDGS

from utils.content_fetcher import fetch_or_screenshot
from utils.cost_tracker import store_cost
from utils.request_analyzer import extract_urls

log = logging.getLogger("research_dispatcher")

API_KEY      = os.environ["OPENROUTER_API_KEY"]
BASE_URL     = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_VISION = os.getenv("MODEL_VISION", "qwen/qwen3-vl-32b-instruct")
MODEL_FAST   = os.getenv("MODEL_FAST",   "deepseek/deepseek-v3.2")
MAX_URLS     = int(os.getenv("RESEARCH_MAX_URLS", "3"))
DDG_REGION   = os.getenv("RESEARCH_DDG_REGION", "de-de")

_HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type":  "application/json",
    "HTTP-Referer":  os.getenv("HTTP_REFERER", "http://localhost:8085"),
    "X-Title":       os.getenv("X_TITLE", "OpenRouter AI Stack"),
}


def _track(model: str, usage: dict, sub_type: str) -> None:
    asyncio.ensure_future(asyncio.to_thread(
        store_cost, model,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        usage.get("cost", 0.0),
        "research", sub_type,
    ))


def _ddg_search(query: str, max_results: int) -> list[dict]:
    """Synchronous DuckDuckGo search — run in thread to avoid blocking."""
    with DDGS() as ddgs:
        return list(ddgs.text(query, region=DDG_REGION, max_results=max_results))


async def _fetch_source(url: str, snippet: str, query: str) -> tuple[str, str]:
    """
    Fetch one URL. Returns (content_block, sub_type).
    Falls back to DDG snippet if fetch fails.
    """
    content, is_visual = await fetch_or_screenshot(url)

    if is_visual:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{BASE_URL}/chat/completions",
                headers=_HEADERS,
                json={"model": MODEL_VISION, "messages": [
                    {"role": "user", "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{content}"}},
                        {"type": "text",
                         "text": f"Extract the key information from this screenshot for: {query}"},
                    ]},
                ]},
            )
            resp.raise_for_status()
            data = resp.json()
        analysis = data["choices"][0]["message"]["content"]
        _track(MODEL_VISION, data.get("usage", {}), "visual")
        return f"Source: {url}\n{analysis}", "visual"

    # Text page — use scraped content, fall back to snippet if scrape failed/empty
    page_text = content if len(content) > 200 else snippet
    return f"Source: {url}\n{page_text}", "text"


async def _synthesize_stream(
    query: str,
    sources: list[str],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> AsyncGenerator[bytes, None]:
    """Stream DeepSeek's synthesized answer from scraped source content."""
    sources_block = "\n\n---\n\n".join(sources)
    synthesis_messages = [
        {"role": "system", "content": (
            "You are a research assistant. Answer the user's question based on "
            "the provided sources. Cite sources (URLs) where relevant. "
            "Be thorough but concise. Answer in the same language as the question."
        )},
        {"role": "user", "content": f"Question: {query}\n\nSources:\n{sources_block}"},
    ]
    payload: dict = {"model": MODEL_FAST, "messages": synthesis_messages, "stream": True}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    t_start = time.monotonic()
    usage: dict = {}

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", f"{BASE_URL}/chat/completions",
                                 headers=_HEADERS, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line == "data: [DONE]":
                    yield b"data: [DONE]\n\n"
                    break
                if not line.startswith("data: "):
                    continue
                try:
                    parsed = json.loads(line[6:])
                    if "usage" in parsed:
                        usage = parsed["usage"]
                except Exception:
                    pass
                yield (line + "\n\n").encode()

    if usage:
        _track(MODEL_FAST, usage, "synthesis")
    log.info("Research done in %dms", int((time.monotonic() - t_start) * 1000))


def _sse(text: str, finish: bool = False) -> bytes:
    chunk = {
        "id": "research-status",
        "object": "chat.completion.chunk",
        "choices": [{"delta": {"content": text} if not finish else {},
                     "finish_reason": "stop" if finish else None,
                     "index": 0}],
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()


async def handle(
    messages: list[dict],
    stream: bool,
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> AsyncGenerator[bytes, None]:
    """Entry point — always yields SSE chunks."""
    user_text = next(
        (m["content"] for m in reversed(messages)
         if m.get("role") == "user" and isinstance(m.get("content"), str)),
        "",
    )

    # ── Step 1: DuckDuckGo search (free, no API key) ─────────
    yield _sse("🔍 Searching DuckDuckGo...\n\n")
    try:
        ddg_results: list[dict] = await asyncio.to_thread(
            _ddg_search, user_text, MAX_URLS
        )
    except Exception as e:
        log.warning("DDG search failed: %s", e)
        yield _sse(f"⚠️ Search error: {e}\n\n")
        yield _sse("", finish=True)
        yield b"data: [DONE]\n\n"
        return

    # Also include any URLs the user explicitly put in their message
    explicit_urls = extract_urls(user_text)
    url_snippet_pairs: list[tuple[str, str]] = [
        (r["href"], r.get("body", "")) for r in ddg_results if r.get("href")
    ]
    for url in explicit_urls:
        if not any(u == url for u, _ in url_snippet_pairs):
            url_snippet_pairs.insert(0, (url, ""))

    url_snippet_pairs = url_snippet_pairs[:MAX_URLS]

    if not url_snippet_pairs:
        yield _sse("No results found.\n\n")
        yield _sse("", finish=True)
        yield b"data: [DONE]\n\n"
        return

    # ── Step 2: Fetch pages in parallel ──────────────────────
    yield _sse(f"📄 Reading {len(url_snippet_pairs)} page(s)...\n\n")
    tasks = [_fetch_source(url, snippet, user_text) for url, snippet in url_snippet_pairs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    sources: list[str] = []
    for (url, _), result in zip(url_snippet_pairs, results):
        if isinstance(result, Exception):
            log.debug("Fetch failed %s: %s", url, result)
            continue
        content_block, sub_type = result
        sources.append(content_block)
        log.info("Fetched %s (%s, %d chars)", url, sub_type, len(content_block))

    if not sources:
        yield _sse("Could not fetch any sources.\n\n")
        yield _sse("", finish=True)
        yield b"data: [DONE]\n\n"
        return

    # ── Step 3: Synthesize via DeepSeek ──────────────────────
    yield _sse("✍️ Synthesizing answer...\n\n---\n\n")
    async for chunk in _synthesize_stream(user_text, sources, temperature, max_tokens):
        yield chunk
