"""
imagegen_dispatcher.py — Image Generation (vorbereitet, noch nicht aktiv)

Status: OpenRouter unterstützt Image-Output aktuell nicht vollständig.
Modelle wie google/gemini-2.5-flash-image berechnen zwar Image-Tokens,
geben die Bilddaten aber nicht in der API-Antwort zurück (Stand: April 2026).

TODO: Aktivieren sobald OpenRouter Image-Output in Chat Completions unterstützt,
      oder alternativen Provider einbinden (DALL-E direkt, Replicate, etc.)
"""

import json
import logging
from typing import AsyncGenerator, Optional

log = logging.getLogger("imagegen_dispatcher")


async def handle(
    messages: list[dict],
    prompt_override: Optional[str] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Placeholder: Bildgenerierung ist vorbereitet aber noch nicht aktiv.
    OpenRouter gibt Image-Output aktuell nicht in der API-Antwort zurück.
    Es wird KEIN API-Call gemacht und KEINE Kosten verursacht.
    """
    log.info("ImageGen: triggered but not yet active (OpenRouter limitation)")
    yield _sse_text(
        "🖼️ Bildgenerierung ist leider noch nicht verfügbar.\n\n"
        "OpenRouter unterstützt das Zurückliefern von generierten Bildern "
        "über die API aktuell noch nicht vollständig — das Feature ist in Vorbereitung.\n\n"
        "**Alternative:** Über das Open WebUI kannst du Bilder generieren, "
        "wenn du unter *Admin Panel → Settings → Images* einen DALL-E API-Key hinterlegst."
    )
    yield b"data: [DONE]\n\n"


def _sse_text(text: str) -> bytes:
    chunk = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
    return f"data: {json.dumps(chunk)}\n\n".encode()
