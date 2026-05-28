"""
LLM-based paragraph classifier.

Returns ONE of three coarse types for a paragraph:
- "quote"    — primarily a direct quotation. Common shape is a sentence (or two)
               followed by an em-dash attribution like "—Author, Source (YYYY)".
- "concept"  — prose explaining or describing ideas to visualise. Default for
               most narrative content.
- "abstract" — pure opinion, transition, or text with no visualisable content.

Why a separate classifier (instead of bundling the decision into
``extract_entities`` / ``extract_concept_cards``):
- A short, focused prompt is much more accurate than a long multi-option prompt.
  ``extract_entities`` previously listed quote detection alongside ~50 lines of
  sentence-by-sentence entity extraction instructions; the LLM, reading all
  those instructions, was biased toward picking "entities" even for short
  quote-shaped paragraphs.
- Splitting classification from extraction means downstream callers only run
  the extractor that matches the verdict (quote / concept / abstract) and each
  extractor's prompt can be tight and specific.

Failure mode: defaults to ``"concept"`` — paragraphs still get a visual
treatment (concept cards) rather than going to a blank-screen scene.
"""
import json
from typing import Final

import requests

from src.utils import logger
from src.config.constants import (
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
)


VALID_TYPES: Final[tuple[str, ...]] = ("quote", "concept", "abstract")


PROMPT = """\
Classify the paragraph below as exactly one of:

- "quote"    — primarily a direct quotation. Often ends with an em-dash
               attribution like "—Author, Source (YYYY)". A paragraph that
               quotes someone else's words is a quote.
- "concept"  — prose explaining, describing, or arguing about ideas. The
               default for narrative text.
- "abstract" — pure opinion, transition, summary, or text with no
               visualisable content (e.g. "In the following chapters we
               will see…").

Paragraph:
"{paragraph}"

Examples:
- "The Internet was done so well that most people think of it as a natural \
resource like the Pacific Ocean, rather than something that was man-made. \
When was the last time a technology with a scale like that was so error-free? \
—Alan Kay, in interview with Dr Dobb's Journal (2012)" → quote
- "The limits of my language mean the limits of my world. \
—Ludwig Wittgenstein, Tractatus Logico-Philosophicus (1922)" → quote
- "Many applications today are data-intensive, as opposed to compute-intensive. \
Raw CPU power is rarely a limiting factor for these applications." → concept
- "Databases store data, queues pass messages, caches speed up reads." → concept
- "In the following chapters we will continue layer by layer." → abstract

Output JSON only:
{{"type": "<quote|concept|abstract>"}}"""


# --- LLM call ---


def _call_ollama(prompt: str) -> str:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            # 4k context is plenty — the prompt + a paragraph fits with room.
            # Smaller context = faster decode on local Ollama.
            "options": {"num_ctx": 4096, "temperature": 0},
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


# --- Public API ---


def classify_paragraph(text: str) -> str:
    """Return ``"quote"``, ``"concept"``, or ``"abstract"`` for ``text``.

    Falls back to ``"concept"`` on LLM failure (safe default — paragraphs still
    get a visual treatment downstream instead of going blank).
    """
    text = (text or "").strip()
    if not text:
        return "abstract"

    prompt = PROMPT.format(paragraph=text)

    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response_text = _call_ollama(prompt)
            data = json.loads(response_text)
            verdict = str(data.get("type", "")).strip().lower()
            if verdict in VALID_TYPES:
                return verdict
            logger.warning(
                f"Classifier returned invalid type '{verdict}', retrying"
            )
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(
                f"Classify attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}"
            )

    logger.warning("Classifier failed; defaulting to 'concept'")
    return "concept"


# --- CLI ---


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description="LLM-based paragraph classifier")
    parser.add_argument("text", type=str, nargs="?", help="Paragraph text to classify")
    parser.add_argument("--file", type=str, help="Read paragraph from a file")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        parser.print_help()
        sys.exit(1)

    print(classify_paragraph(text))
