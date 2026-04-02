"""
content_fetcher.py — Fetch and extract content from URLs.

Single responsibility: given a URL, return clean text or raw bytes.
Hybrid approach: detect visual sites → screenshot; text sites → extract.
Puppeteer is optional — falls back to text extraction if unavailable.
"""

import asyncio
import logging
import re
from typing import Optional

import httpx

log = logging.getLogger("content_fetcher")

VISUAL_PATH_HINTS = {"/dashboard", "/analytics", "/charts", "/graph", "/report", "/stats"}
VISUAL_META_HINTS = {"canvas", "chart.js", "highcharts", "d3.js", "plotly"}

FETCH_TIMEOUT = 10.0
MAX_TEXT_LENGTH = 8000


def _is_likely_visual(url: str, html: str = "") -> bool:
    """Heuristic: does this URL/page contain mostly visual/chart content?"""
    url_lower = url.lower()
    if any(hint in url_lower for hint in VISUAL_PATH_HINTS):
        return True
    html_lower = html.lower()
    return sum(1 for hint in VISUAL_META_HINTS if hint in html_lower) >= 2


def _extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML, stripping tags and boilerplate."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:MAX_TEXT_LENGTH]
    except ImportError:
        # BeautifulSoup not installed — basic tag strip
        clean = re.sub(r"<[^>]+>", " ", html)
        clean = re.sub(r"\s{2,}", " ", clean)
        return clean[:MAX_TEXT_LENGTH]


async def fetch_text(url: str) -> Optional[str]:
    """Fetch URL and return extracted plain text. Returns None on failure."""
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OpenRouterBot/1.0)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
            return _extract_text_from_html(html)
    except Exception as e:
        log.debug("fetch_text failed for %s: %s", url, e)
        return None


async def screenshot_url(url: str) -> Optional[bytes]:
    """
    Take a screenshot of the URL using pyppeteer.
    Returns JPEG bytes or None if pyppeteer/Chromium is unavailable.
    """
    try:
        import os
        from pyppeteer import launch

        executable = os.getenv("PUPPETEER_EXECUTABLE_PATH", "/usr/bin/chromium")
        browser = await launch(
            executablePath=executable,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.newPage()
        await page.setViewport({"width": 1280, "height": 900})
        await page.goto(url, {"waitUntil": "networkidle2", "timeout": 20000})
        img_bytes = await page.screenshot({"type": "jpeg", "quality": 80})
        await browser.close()
        return img_bytes
    except ImportError:
        log.debug("pyppeteer not available — screenshot skipped for %s", url)
        return None
    except Exception as e:
        log.debug("screenshot_url failed for %s: %s", url, e)
        return None


async def fetch_or_screenshot(url: str) -> tuple[str, bool]:
    """
    Return (content, is_visual).
    - Tries text fetch first
    - If page looks visual and pyppeteer is available, returns base64 screenshot marker
    """
    html = ""
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OpenRouterBot/1.0)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        log.debug("Initial fetch failed for %s: %s", url, e)
        return f"[Could not fetch {url}]", False

    if _is_likely_visual(url, html):
        img_bytes = await screenshot_url(url)
        if img_bytes:
            import base64
            return base64.b64encode(img_bytes).decode(), True

    return _extract_text_from_html(html), False
