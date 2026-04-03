"""
imagegen_dispatcher.py — Image Generation via Together.ai FLUX

Uses Together.ai FLUX.1-schnell-Free (free tier) to generate images.
Requires TOGETHER_API_KEY — when absent, returns a friendly error message.

Pricing: FLUX.1-schnell-Free is free in Together.ai's free tier.
         Paid alternative: black-forest-labs/FLUX.1-schnell (~$0.0006/img)
"""

import json
import logging
import os
from typing import AsyncGenerator, Optional

import httpx

log = logging.getLogger("imagegen_dispatcher")

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_IMG_URL = "https://api.together.xyz/v1/images/generations"
MODEL_IMAGEGEN   = os.getenv("MODEL_IMAGEGEN", "black-forest-labs/FLUX.1-schnell-Free")

IMG_WIDTH  = int(os.getenv("IMAGEGEN_WIDTH", "1024"))
IMG_HEIGHT = int(os.getenv("IMAGEGEN_HEIGHT", "1024"))


def _extract_prompt(messages: list[dict], prompt_override: Optional[str]) -> str:
    """Extract the image prompt from the last user message or override."""
    if prompt_override:
        return prompt_override
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        return part.get("text", "")
    return "A beautiful image"


async def handle(
    messages: list[dict],
    prompt_override: Optional[str] = None,
) -> AsyncGenerator[bytes, None]:
    """Generate an image via Together.ai and stream the result as SSE."""
    if not TOGETHER_API_KEY:
        yield _sse_text(
            "🖼️ Bildgenerierung ist noch nicht aktiv.\n\n"
            "Um Bilder generieren zu können, trage einen `TOGETHER_API_KEY` in die `.env` Datei ein.\n"
            "Kostenlose Registrierung unter https://api.together.ai — "
            "das Free-Tier beinhaltet FLUX.1-schnell ohne Kosten."
        )
        yield b"data: [DONE]\n\n"
        return

    prompt = _extract_prompt(messages, prompt_override)
    log.info("ImageGen: generating image for prompt='%s...' via %s", prompt[:60], MODEL_IMAGEGEN)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                TOGETHER_IMG_URL,
                headers={
                    "Authorization": f"Bearer {TOGETHER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model":  MODEL_IMAGEGEN,
                    "prompt": prompt,
                    "n":      1,
                    "width":  IMG_WIDTH,
                    "height": IMG_HEIGHT,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        image_url = data["data"][0].get("url", "")
        if not image_url:
            raise ValueError("No image URL in Together.ai response")

        log.info("ImageGen: image generated → %s", image_url[:80])
        yield _sse_text(f"![Generiertes Bild]({image_url})")

    except httpx.HTTPStatusError as e:
        log.error("ImageGen API error: %s", e)
        yield _sse_text(
            f"❌ Bildgenerierung fehlgeschlagen (HTTP {e.response.status_code}).\n"
            "Bitte TOGETHER_API_KEY in der `.env` Datei prüfen."
        )
    except Exception as e:
        log.error("ImageGen unexpected error: %s", e)
        yield _sse_text(f"❌ Bildgenerierung fehlgeschlagen: {e}")

    yield b"data: [DONE]\n\n"


def _sse_text(text: str) -> bytes:
    chunk = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
    return f"data: {json.dumps(chunk)}\n\n".encode()
