"""
LLM-based quote extractor.

Given a paragraph that's already been classified as a quote (via
``llm_classifier.classify_paragraph``), pull the quote body and attribution
into separate fields for ``SHOW_QUOTE`` rendering.

Why this is its own module: the original combined ``llm_entity.py`` bundled
quote detection with entity extraction in one prompt. We split classification
from extraction in fix #3 — each focused prompt is shorter and more accurate
than the do-everything version. The entity-extraction half of that module is
no longer used (no scene type spawns the old SHOW_ENTITY visual), so this
module is now the only live remnant.

Public API:
- ``QuoteResult`` — dataclass with ``text`` and ``attribution`` fields.
- ``extract_quote(paragraph_text)`` — returns a ``QuoteResult``. Falls back
  to ``(paragraph_text, "")`` on LLM failure so the raw paragraph still
  renders as a quote rather than disappearing.
"""
import json
from dataclasses import dataclass

import requests

from src.utils import logger
from src.config.constants import (
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
)


@dataclass
class QuoteResult:
    """Focused extraction result for paragraphs already classified as ``quote``.

    `text` is the quote itself (without surrounding quote marks). `attribution`
    is whatever comes after the em-dash — author name, source, year — joined
    into one display string. Empty `attribution` is valid when the source has
    only the quote with no attribution line.
    """
    text: str = ""
    attribution: str = ""


QUOTE_PROMPT = """\
The paragraph below is a direct quotation, often followed by an attribution.

Paragraph:
"{paragraph}"

Extract:
- "text"        — the quoted content itself, WITHOUT surrounding quote marks.
                  Strip any leading/trailing whitespace. Preserve internal
                  punctuation as-is.
- "attribution" — the author / source / year that follows the em-dash, joined
                  into one human-readable string. Empty string if no
                  attribution line is present.

Example input:
"The Internet was done so well that most people think of it as a natural \
resource like the Pacific Ocean, rather than something that was man-made. \
When was the last time a technology with a scale like that was so error-free? \
—Alan Kay, in interview with Dr Dobb's Journal (2012)"

Example output:
{{"text": "The Internet was done so well that most people think of it as a \
natural resource like the Pacific Ocean, rather than something that was \
man-made. When was the last time a technology with a scale like that was so \
error-free?", "attribution": "Alan Kay, in interview with Dr Dobb's Journal (2012)"}}

Output JSON only:
{{"text": "<quote without surrounding quotes>", "attribution": "<author, source, year — or empty>"}}"""


def _call_ollama(prompt: str) -> str:
    """Single Ollama round-trip. Tight context window — quote-extraction
    prompt + a paragraph fits comfortably under 4k tokens."""
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": 4096, "temperature": 0},
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def extract_quote(paragraph_text: str) -> QuoteResult:
    """Pull the quote body + attribution out of a paragraph already known
    to be a quote (use ``llm_classifier.classify_paragraph`` upstream to
    decide). Falls back to ``(paragraph_text, "")`` on LLM failure so the
    raw paragraph still renders as a quote rather than disappearing.
    """
    text = (paragraph_text or "").strip()
    if not text:
        return QuoteResult()

    prompt = QUOTE_PROMPT.format(paragraph=text)

    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response_text = _call_ollama(prompt)
            data = json.loads(response_text)
            quote_text = str(data.get("text", "")).strip().strip('"').strip("“”")
            attribution = str(data.get("attribution", "")).strip()
            if quote_text:
                return QuoteResult(text=quote_text, attribution=attribution)
            logger.warning("Quote extraction returned empty text, retrying")
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(
                f"Quote extraction attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}"
            )

    logger.warning("Quote extraction failed; using paragraph as-is")
    return QuoteResult(text=text, attribution="")


if __name__ == "__main__":
    import sys
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="LLM-based quote extractor")
    parser.add_argument("text", type=str, nargs="?", help="Paragraph text to extract")
    parser.add_argument("--file", type=str, help="Read paragraph from a file")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        parser.print_help()
        sys.exit(1)

    result = extract_quote(text)
    print(f"text: {result.text!r}")
    print(f"attribution: {result.attribution!r}")
