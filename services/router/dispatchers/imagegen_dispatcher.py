"""
imagegen_dispatcher.py — Image Generation via OpenRouter

Triggers: /imagegen, /bild, /generate + image keywords
Model: configured via MODEL_IMAGEGEN env var (default: flux or similar)
Returns: SSE stream with the generated image as markdown
"""

import os
import logging
import httpx
from typing import AsyncGenerator, Optional

log = logging.getLogger("imagegen_dispatcher")

API_KEY        = os.environ["OPENROUTER_API_KEY"]
BASE_URL       = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_IMAGEGEN = os.getenv("MODEL_IMAGEGEN", "black-forest-labs/flux-schnell")


def _or_headers() -> dict:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def _extract_prompt(text: str) -> str:
    """Strip dispatch commands from user text to get the image prompt."""
    commands = ["/imagegen", "/bild", "/generate", "/erstelle-bild"]
    cleaned = text
    for cmd in commands:
        cleaned = cleaned.replace(cmd, "")
    return cleaned.strip() or text.strip()


async def handle(
    messages: list[dict],
    prompt_override: Optional[str] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Generate an image and stream the result as SSE.
    Uses OpenRouter's image generation endpoint (OpenAI-compatible).
    """
    # Extract prompt from last user message
    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    if isinstance(last_user, list):
        last_user = " ".join(p.get("text", "") for p in last_user if p.get("type") == "text")

    image_prompt = prompt_override or _extract_prompt(last_user)
    log.info("ImageGen: prompt='%.80s' model=%s", image_prompt, MODEL_IMAGEGEN)

    # Yield status header
    yield _sse_text(f"🎨 Generiere Bild mit `{MODEL_IMAGEGEN}`...\n\n")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{BASE_URL}/images/generations",
                headers=_or_headers(),
                json={
                    "model": MODEL_IMAGEGEN,
                    "prompt": image_prompt,
                    "n": 1,
                    "response_format": "url",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        images = data.get("data", [])
        if not images:
            yield _sse_text("❌ Kein Bild generiert — leere Antwort vom Modell.")
            yield b"data: [DONE]\n\n"
            return

        for img in images:
            url = img.get("url", "")
            revised = img.get("revised_prompt", "")
            if url:
                yield _sse_text(f"![Generiertes Bild]({url})\n\n")
                if revised and revised != image_prompt:
                    yield _sse_text(f"*Prompt angepasst: {revised}*\n")

    except httpx.HTTPStatusError as e:
        log.error("ImageGen HTTP error %d: %s", e.response.status_code, e.response.text)
        yield _sse_text(f"❌ Fehler {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        log.error("ImageGen error: %s", e)
        yield _sse_text(f"❌ Fehler bei der Bildgenerierung: {e}")

    yield b"data: [DONE]\n\n"


def _sse_text(text: str) -> bytes:
    """Wrap text as an OpenAI-compatible SSE delta chunk."""
    import json
    chunk = {
        "choices": [{"delta": {"content": text}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()
