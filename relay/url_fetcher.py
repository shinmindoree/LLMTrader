"""Fetch and extract readable text content from URLs found in user messages.

When users paste URLs in strategy chat or generation prompts, this module
detects them, fetches the page content, and injects the extracted text
into the conversation so the LLM can reference it.
"""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser

import httpx

logger = logging.getLogger(__name__)

# Match http/https URLs in text
_URL_PATTERN = re.compile(
    r"https?://[^\s<>\"\'\)\]\}]+",
    re.IGNORECASE,
)

# Maximum characters to extract per URL
_MAX_CHARS_PER_URL = 4000

# Maximum number of URLs to fetch per message
_MAX_URLS = 3

# Request timeout (seconds)
_FETCH_TIMEOUT = 10.0

# Block-level tags that should produce line breaks
_BLOCK_TAGS = frozenset({
    "p", "div", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "tr", "blockquote", "pre", "section", "article", "header",
    "footer", "nav", "main", "aside", "figure", "figcaption", "dt", "dd",
})

# Tags whose content should be skipped entirely
_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "svg", "iframe", "template",
    "head",
})


class _HTMLTextExtractor(HTMLParser):
    """Simple HTML-to-text converter using only stdlib."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS and self._skip_depth == 0:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS and self._skip_depth == 0:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(html.unescape(f"&#{name};"))

    def get_text(self) -> str:
        raw = "".join(self._chunks)
        # Collapse multiple blank lines into one
        lines = raw.splitlines()
        cleaned: list[str] = []
        prev_blank = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if not prev_blank:
                    cleaned.append("")
                prev_blank = True
            else:
                cleaned.append(stripped)
                prev_blank = False
        return "\n".join(cleaned).strip()


def extract_urls(text: str) -> list[str]:
    """Extract unique URLs from text, up to _MAX_URLS."""
    if not text:
        return []
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,;:!?)")
        if url not in seen:
            seen.add(url)
            urls.append(url)
            if len(urls) >= _MAX_URLS:
                break
    return urls


def _html_to_text(html_content: str) -> str:
    """Convert HTML to plain text using stdlib HTMLParser."""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html_content)
    except Exception:
        return ""
    return parser.get_text()


async def fetch_url_content(url: str) -> str | None:
    """Fetch a URL and return extracted text content, or None on failure."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_FETCH_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            },
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type or "xhtml" in content_type:
            text = _html_to_text(resp.text)
            # Detect Cloudflare / bot-challenge pages
            if not text or len(text) < 50 or "just a moment" in text.lower():
                logger.info("URL returned challenge/empty page: %s", url)
                return None
        elif "text/plain" in content_type:
            text = resp.text.strip()
        else:
            logger.info("Skipping non-text URL %s (content-type: %s)", url, content_type)
            return None

        if not text:
            return None

        # Truncate to max chars
        if len(text) > _MAX_CHARS_PER_URL:
            text = text[:_MAX_CHARS_PER_URL] + "\n\n[... 페이지 내용이 잘렸습니다]"

        return text
    except httpx.TimeoutException:
        logger.warning("URL fetch timed out: %s", url)
        return None
    except httpx.HTTPStatusError as e:
        logger.warning("URL fetch HTTP error %d: %s", e.response.status_code, url)
        return None
    except Exception:
        logger.warning("URL fetch failed: %s", url, exc_info=True)
        return None


async def fetch_urls_from_text(text: str) -> list[tuple[str, str]]:
    """Extract URLs from text and fetch their content.

    Returns list of (url, content) tuples for successfully fetched URLs.
    """
    urls = extract_urls(text)
    if not urls:
        return []

    results: list[tuple[str, str]] = []
    for url in urls:
        content = await fetch_url_content(url)
        if content:
            results.append((url, content))
            logger.info("Fetched URL content: %s (%d chars)", url, len(content))

    return results


def inject_url_content_into_messages(
    messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Placeholder for sync path — URL injection happens in async callers."""
    return messages


async def enrich_messages_with_url_content(
    messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Detect URLs in the last user message and inject fetched content.

    If URLs are found in the last user message, their content is appended
    as context so the LLM can reference the page content.
    """
    if not messages:
        return messages

    # Find the last user message
    last_user_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    if last_user_idx is None:
        return messages

    user_content = messages[last_user_idx].get("content", "")
    fetched = await fetch_urls_from_text(user_content)
    if not fetched:
        return messages

    # Build enriched message with URL content appended
    url_sections: list[str] = []
    for url, content in fetched:
        url_sections.append(f"---\n📄 [{url}] 페이지 내용:\n\n{content}\n---")

    enriched_content = (
        f"{user_content}\n\n"
        "아래는 위 URL에서 추출한 페이지 내용입니다:\n\n"
        + "\n\n".join(url_sections)
    )

    # Return new list with the enriched user message
    enriched = list(messages)
    enriched[last_user_idx] = {
        **messages[last_user_idx],
        "content": enriched_content,
    }
    return enriched
