"""
request_analyzer.py — Detect special dispatch triggers in incoming requests.

Single responsibility: classify requests, return trigger flags.
No I/O, no side effects — pure detection logic.
"""

import re
from typing import Optional

# ── Research triggers ─────────────────────────────────────────

RESEARCH_COMMANDS = {"/recherchiere", "/research", "/search", "/suche"}

RESEARCH_KEYWORDS = {
    # German — only explicit news/research intent
    "nachrichten", "aktueller stand", "breaking news",
    "was ist passiert", "neueste meldungen",
    # English — only explicit news/research intent
    "breaking news", "what happened", "latest news",
    "what's new", "whats new",
}

URL_PATTERN = re.compile(r"https?://\S+")


def detect_research_trigger(text: str) -> bool:
    """Return True if the message should be routed to the research dispatcher."""
    lower = text.lower()

    if any(cmd in lower for cmd in RESEARCH_COMMANDS):
        return True

    if URL_PATTERN.search(text):
        return True

    words = set(lower.split())
    return bool(words & RESEARCH_KEYWORDS)


def extract_urls(text: str) -> list[str]:
    """Return all URLs found in the text."""
    return URL_PATTERN.findall(text)


# ── Image-gen triggers ────────────────────────────────────────

IMAGEGEN_COMMANDS = {"/imagegen", "/generate", "/bild", "/erstelle-bild"}

# Exact substring phrases
IMAGEGEN_PHRASES = {
    "generate image", "create image", "erstelle ein bild", "generiere ein bild",
    "make an image", "als bild generieren", "als bild erstellen",
    "ein bild von", "ein foto von",
}

# Verb + noun word-pair detection (both must appear as separate words)
_IMG_VERBS = {"generiere", "generier", "zeichne", "erstelle", "male", "draw", "create", "generate"}
_IMG_NOUNS = {"bild", "image", "foto", "grafik", "illustration", "photo", "picture"}


def detect_imagegen_trigger(text: str) -> tuple[bool, float]:
    """Return (triggered, confidence)."""
    lower = text.lower()
    if any(cmd in lower for cmd in IMAGEGEN_COMMANDS):
        return True, 1.0
    if any(phrase in lower for phrase in IMAGEGEN_PHRASES):
        return True, 0.9
    words = set(lower.split())
    if words & _IMG_VERBS and words & _IMG_NOUNS:
        return True, 0.8
    return False, 0.0


# ── Audio triggers ────────────────────────────────────────────

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm"}
AUDIO_COMMANDS = {"/transkribiere", "/transcribe"}

AUDIO_DISPLAY_MODE = "visible"
AUDIO_SILENT_MODE = "silent"


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
