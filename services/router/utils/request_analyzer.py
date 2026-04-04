"""
request_analyzer.py — Utility functions for request inspection.

Note: image generation and web search are now handled via LLM tool calling.
This module retains only the audio-trigger detection (used for file-upload
endpoints) and URL extraction (used by content fetchers).
"""

import re

# ── URL extraction ────────────────────────────────────────────

URL_PATTERN = re.compile(r"https?://\S+")


def extract_urls(text: str) -> list[str]:
    """Return all URLs found in the text."""
    return URL_PATTERN.findall(text)


# ── Audio triggers ────────────────────────────────────────────

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm"}
AUDIO_COMMANDS   = {"/transkribiere", "/transcribe"}

AUDIO_DISPLAY_MODE = "visible"
AUDIO_SILENT_MODE  = "silent"


def detect_audio_trigger(text: str, has_audio_file: bool = False) -> tuple[bool, str]:
    """Return (triggered, display_mode: 'visible'|'silent')."""
    lower = text.lower()

    if any(cmd in lower for cmd in AUDIO_COMMANDS):
        return True, AUDIO_DISPLAY_MODE

    if has_audio_file:
        return True, AUDIO_SILENT_MODE

    if any(text.lower().endswith(ext) for ext in AUDIO_EXTENSIONS):
        return True, AUDIO_SILENT_MODE

    return False, AUDIO_SILENT_MODE
