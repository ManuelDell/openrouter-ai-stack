"""
audio_dispatcher.py — Audio transcription via self-hosted Whisper service.

Single responsibility: send audio to Whisper, store transcript in memory,
return to user in the configured display mode.

display_mode:
  silent  — transcript stored in memory, hidden from user (default)
  visible — transcript shown to user (triggered by /transkribiere)
"""

import json
import logging
import os
from typing import AsyncGenerator, Optional

import httpx

from utils.cost_tracker import store_cost

log = logging.getLogger("audio_dispatcher")

WHISPER_URL = os.getenv("WHISPER_URL", "http://ai-whisper:8093")
MEMORY_URL  = os.getenv("MEMORY_SERVICE_URL", "http://memory-svc:8081")


async def transcribe(audio_bytes: bytes, filename: str, language: Optional[str] = None) -> str:
    """Send audio file to Whisper service, return transcript text."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{WHISPER_URL}/v1/audio/transcriptions",
            files={"file": (filename, audio_bytes, "audio/mpeg")},
            data={"language": language or "", "response_format": "json"},
        )
        resp.raise_for_status()
        return resp.json().get("text", "")


async def _store_transcript(query_hint: str, transcript: str) -> None:
    """Persist transcript in memory service for future context retrieval."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{MEMORY_URL}/store",
                json={"query": f"[audio] {query_hint[:200]}",
                      "response": transcript[:2000],
                      "metadata": {"type": "audio_transcript"}},
            )
    except Exception as e:
        log.debug("Memory store failed (non-fatal): %s", e)


def _sse(text: str, finish: bool = False) -> bytes:
    chunk = {
        "id": "audio-0",
        "object": "chat.completion.chunk",
        "choices": [{"delta": {"content": text} if not finish else {},
                     "finish_reason": "stop" if finish else None,
                     "index": 0}],
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()


async def handle(
    audio_bytes: bytes,
    filename: str,
    display_mode: str,       # "silent" | "visible"
    context_hint: str = "",  # user's text alongside the audio
    language: Optional[str] = None,
) -> AsyncGenerator[bytes, None]:
    """Transcribe audio and return SSE stream."""
    yield _sse("🎙️ Transcribing audio...\n\n")

    try:
        transcript = await transcribe(audio_bytes, filename, language)
    except Exception as e:
        log.warning("Transcription failed: %s", e)
        yield _sse(f"⚠️ Transcription error: {e}\n\n")
        yield _sse("", finish=True)
        yield b"data: [DONE]\n\n"
        return

    # Always store in memory
    import asyncio
    asyncio.ensure_future(_store_transcript(context_hint or filename, transcript))

    # Cost tracking: Whisper is self-hosted so cost = 0, but track the event
    asyncio.ensure_future(asyncio.to_thread(
        store_cost, "whisper/self-hosted", 0, 0, 0.0, "audio", display_mode
    ))

    if display_mode == "visible":
        yield _sse(f"**Transcript:**\n\n{transcript}\n\n")
    else:
        yield _sse("✅ Audio transcribed and stored in memory.\n\n")

    yield _sse("", finish=True)
    yield b"data: [DONE]\n\n"
