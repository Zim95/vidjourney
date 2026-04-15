"""
LLM-based entity extractor.

For paragraphs without resources, classify the paragraph as one of:
- quote: a direct quotation → display the quote text
- entities: concrete visual entities + relationships → spawn icons + draw arrows
- abstract: too abstract to visualize → fall back to blank display

Used by llm_timeline.py to populate the visuals for paragraph-only scenes.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests

from src.utils import logger
from src.config.constants import (
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
)


PROMPT = """\
You are analyzing a paragraph from an educational text to determine how to visualize it during narration.

Paragraph:
"{paragraph}"

Classify the paragraph and respond with JSON only.

Option 1: If the paragraph is primarily a direct quotation:
{{"type": "quote", "text": "<the quote without surrounding quotes>", "attribution": "<who said it, or empty string>"}}

Option 2: If the paragraph describes concrete visual entities and their relationships:
{{"type": "entities", "spawns": ["entity1", "entity2"], "arrows": [{{"from": "entity1", "verb": "verb", "to": "entity2"}}]}}

Option 3: If the paragraph is too abstract to visualize (concepts, opinions, definitions, with no concrete things):
{{"type": "abstract"}}

Rules:
- Only extract concrete things that can be shown as icons (e.g. database, server, client, queue, file, process). Do not extract abstract concepts (e.g. reliability, scalability, performance, simplicity).
- Use short generic entity names (e.g. "database", not "PostgreSQL relational database management system").
- Only include arrows for clear relationships where a verb directly connects two named entities.
- Verbs should be 1-2 words (e.g. "queries", "writes to", "depends on").
- Maximum 6 entities per paragraph. Pick the most important ones if there are more.
- If you are unsure or the paragraph is mostly prose without concrete things, return {{"type": "abstract"}}.
- Do not force entities or arrows when none clearly apply."""


@dataclass
class EntityResult:
    type: str                          # "quote" | "entities" | "abstract"
    text: str = ""                     # quote text (if quote)
    attribution: str = ""              # quote attribution (if quote)
    spawns: list[str] = None           # entity names (if entities)
    arrows: list[dict] = None          # [{"from": str, "verb": str, "to": str}] (if entities)


def _call_ollama(prompt: str) -> str:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": 8192, "temperature": 0},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _coerce_str_list(raw) -> list[str]:
    """Coerce LLM output to a list of strings, dropping garbage."""
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def _coerce_arrows(raw) -> list[dict]:
    """Coerce arrow specs, dropping malformed entries."""
    if not isinstance(raw, list):
        return []
    result = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        src = a.get("from")
        verb = a.get("verb")
        dst = a.get("to")
        if isinstance(src, str) and isinstance(dst, str) and src.strip() and dst.strip():
            result.append({
                "from": src.strip(),
                "verb": (verb.strip() if isinstance(verb, str) else "") or "relates to",
                "to": dst.strip(),
            })
    return result


def _parse_response(response_text: str) -> EntityResult:
    data = json.loads(response_text)
    type_ = data.get("type", "abstract")

    if type_ == "quote":
        return EntityResult(
            type="quote",
            text=str(data.get("text", "")).strip().strip('"'),
            attribution=str(data.get("attribution", "")).strip(),
        )
    if type_ == "entities":
        spawns = _coerce_str_list(data.get("spawns"))
        arrows = _coerce_arrows(data.get("arrows"))
        # Filter arrows: both endpoints must be in spawns
        spawn_set = set(s.lower() for s in spawns)
        arrows = [a for a in arrows if a["from"].lower() in spawn_set and a["to"].lower() in spawn_set]
        if not spawns:
            return EntityResult(type="abstract")
        return EntityResult(type="entities", spawns=spawns, arrows=arrows)
    return EntityResult(type="abstract")


def extract(paragraph_text: str) -> EntityResult:
    """Extract entities, quote, or abstract classification from a paragraph."""
    prompt = PROMPT.format(paragraph=paragraph_text)

    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response_text = _call_ollama(prompt)
            return _parse_response(response_text)
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(f"Entity extraction attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

    logger.warning("Entity extraction failed, treating as abstract")
    return EntityResult(type="abstract")


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="LLM-based entity extractor")
    parser.add_argument("text", type=str, nargs="?", help="Paragraph text to analyze")
    parser.add_argument("--file", type=str, help="Read paragraph from a file")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        parser.print_help()
        sys.exit(1)

    result = extract(text)
    print(f"Type: {result.type}")
    if result.type == "quote":
        print(f"Text: {result.text}")
        print(f"Attribution: {result.attribution}")
    elif result.type == "entities":
        print(f"Spawns: {result.spawns}")
        print(f"Arrows: {result.arrows}")
