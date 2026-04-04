"""Tool execution handlers for LLM function calling.

Each handler receives parsed arguments and returns a plain string result
that is appended to the message history and fed back to the LLM.
No SSE, no streaming — pure request/response.
"""

import asyncio
import logging
import os
from typing import Any

import httpx

from utils.content_fetcher import fetch_or_screenshot

log = logging.getLogger("tool_executor")

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_IMG_URL = "https://api.together.xyz/v1/images/generations"
MODEL_IMAGEGEN   = os.getenv("MODEL_IMAGEGEN", "black-forest-labs/FLUX.1-schnell")
IMG_WIDTH        = int(os.getenv("IMAGEGEN_WIDTH", "1024"))
IMG_HEIGHT       = int(os.getenv("IMAGEGEN_HEIGHT", "1024"))

SEARXNG_URL      = os.getenv("SEARXNG_URL", "http://searxng:8080")
MAX_URLS         = int(os.getenv("RESEARCH_MAX_URLS", "3"))

BASH_EXECUTOR_URL = os.getenv("BASH_EXECUTOR_URL", "http://bash-executor:8090")


# ── Image Generation ──────────────────────────────────────────

async def _expand_url(url: str) -> str:
    """Follow redirects to get the final CDN URL (Together.ai returns short URLs)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.head(url)
            return str(resp.url)
    except Exception:
        return url


async def _tool_generate_image(prompt: str) -> str:
    if not TOGETHER_API_KEY:
        return "Image generation is not configured (TOGETHER_API_KEY missing)."

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            TOGETHER_IMG_URL,
            headers={"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL_IMAGEGEN, "prompt": prompt, "n": 1,
                  "width": IMG_WIDTH, "height": IMG_HEIGHT},
        )
        resp.raise_for_status()
        data = resp.json()

    image_url = data["data"][0].get("url", "")
    if not image_url:
        return "Image generation failed: no URL in response."

    # Expand short URLs so browsers can render the image directly
    image_url = await _expand_url(image_url)
    log.info("Tool generate_image → %s", image_url[:80])
    return image_url  # Return bare URL — router will embed as markdown


# ── Web Search ────────────────────────────────────────────────

async def _tool_web_search(query: str) -> str:
    log.info("Tool web_search: %r", query[:80])

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json", "language": "de",
                        "max_results": MAX_URLS},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
    except Exception as e:
        log.warning("SearXNG search failed: %s", e)
        return f"Web search failed: {e}"

    url_snippets = [(r["url"], r.get("content", "")) for r in results if r.get("url")][:MAX_URLS]
    if not url_snippets:
        return "No search results found."

    tasks = [fetch_or_screenshot(url) for url, _ in url_snippets]
    fetched = await asyncio.gather(*tasks, return_exceptions=True)

    sources: list[str] = []
    for (url, snippet), result in zip(url_snippets, fetched):
        if isinstance(result, Exception):
            if snippet:
                sources.append(f"Source: {url}\n{snippet}")
        else:
            content, is_visual = result
            page_text = content if not is_visual and len(content) > 200 else snippet
            if page_text:
                sources.append(f"Source: {url}\n{page_text[:3000]}")

    return "\n\n---\n\n".join(sources) if sources else "Could not fetch any search results."


# ── Bash Execution ────────────────────────────────────────────

async def _tool_bash(command: str, timeout: int = 30) -> str:
    try:
        async with httpx.AsyncClient(timeout=float(timeout + 10)) as client:
            resp = await client.post(
                f"{BASH_EXECUTOR_URL}/exec",
                json={"command": command, "timeout": timeout},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        return "[bash-executor nicht erreichbar — Service läuft noch nicht oder ist ausgefallen]"
    except Exception as e:
        return f"[bash-executor Fehler: {e}]"

    stdout   = data.get("stdout", "").strip()
    stderr   = data.get("stderr", "").strip()
    exit_code = data.get("exit_code", 0)
    truncated = data.get("truncated", False)
    cwd      = data.get("cwd", "")

    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if not parts:
        parts.append("(kein Output)")
    if exit_code != 0:
        parts.append(f"[exit code: {exit_code}]")
    if truncated:
        parts.append("[Output wurde gekürzt — nutze head/tail/grep für gezielte Ausgabe]")
    if cwd and cwd != "/workspace":
        parts.append(f"[cwd: {cwd}]")

    return "\n".join(parts)


async def _tool_reset_bash() -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{BASH_EXECUTOR_URL}/reset")
            resp.raise_for_status()
            return resp.json().get("message", "Bash-Session zurückgesetzt")
    except Exception as e:
        return f"Reset fehlgeschlagen: {e}"


# ── Dispatcher ────────────────────────────────────────────────

async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Dispatch a tool call by name and return the string result."""
    if name == "generate_image":
        return await _tool_generate_image(arguments.get("prompt", ""))
    if name == "web_search":
        return await _tool_web_search(arguments.get("query", ""))
    if name == "bash":
        return await _tool_bash(
            arguments.get("command", ""),
            arguments.get("timeout", 30),
        )
    if name == "reset_bash":
        return await _tool_reset_bash()
    return f"Unknown tool: {name}"
