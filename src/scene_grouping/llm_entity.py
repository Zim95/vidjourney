"""
LLM-based entity extractor.

For paragraphs without resources, classify the paragraph as one of:
- quote: a direct quotation → display the quote text
- entities: visualizable items, tagged by sentence index → spawn shapes/icons
            in sync with what the narrator is saying
- abstract: too abstract to visualize → fall back to blank display

Entities carry a `kind` so downstream display can route appropriately:
- "concrete" — has a recognizable visual representation (database, server, client)
- "abstract" — concept or property (reliability, scalability, consistency)
- "action"   — verb / process (query, write, replicate, invalidate)

…and a `sentence` index so the timeline builder can anchor each entity's
SPAWN to where its sentence starts in the narration. At least one entity
is requested per sentence so the screen always has something to display.
"""
import json
import re
from dataclasses import dataclass, field
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

First decide what kind of paragraph this is.

Option 1: If the paragraph is primarily a direct quotation:
{{"type": "quote", "text": "<the quote without surrounding quotes>", "attribution": "<who said it, or empty string>"}}

Option 2: Otherwise, treat it as visualizable. The paragraph has been split below
into numbered sentences. For EACH sentence produce 1-3 short entities to spawn on
screen as the narrator reads it. The screen should always have something to display
— include AT LEAST ONE entity per sentence, even for short connector sentences.

Numbered sentences:
{numbered_sentences}

Entity kinds — choose one per entity:
- "concrete" — a thing with a recognizable visual (database, server, client, API, queue, file, message)
- "abstract" — concept, property, or quality (reliability, scalability, consistency, complexity, durability)
- "action"   — verb or ongoing process (query, replicate, invalidate, deploy, update)

Output JSON:
{{"type": "entities", "entities": [{{"name": "<phrase>", "kind": "<kind>", "sentence": <int>}}, ...]}}

Rules:
- Each entity name is a 1-3 word phrase that ACTUALLY APPEARS (or is clearly implied) in its sentence.
- Do not invent synonyms. Quote the source words.
- Reuse the same entity across sentences only when the sentence really repeats it.
- 1-3 entities per sentence (1 is fine for short ones); never zero.

Option 3: If the paragraph contains zero visualizable content (pure abstract opinion):
{{"type": "abstract"}}"""


VALID_KINDS = {"concrete", "abstract", "action"}


@dataclass
class Entity:
    name: str
    kind: str               # "concrete" | "abstract" | "action"
    sentence: int = 0       # which sentence (0-based) this entity belongs to


@dataclass
class EntityResult:
    type: str                                       # "quote" | "entities" | "abstract"
    text: str = ""                                  # quote text (if quote)
    attribution: str = ""                           # quote attribution (if quote)
    entities: list[Entity] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)  # the sentence split used for this paragraph
    arrows: list[dict] = field(default_factory=list)


# --- Sentence splitting (kept consistent with subtitle/timeline word logic) ---

def split_sentences(text: str) -> list[str]:
    """Split a paragraph into sentences. Same regex other parts of the pipeline use."""
    cleaned = re.sub(r"[‐­]\s+", "", text)         # PDF soft hyphens
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", cleaned)
    return [s.strip() for s in sentences if s.strip()]


# --- LLM call ---

def _call_ollama(prompt: str) -> str:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": 16384, "temperature": 0},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


# --- Response parsing ---

def _coerce_entity(item, n_sentences: int) -> Entity | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    kind = item.get("kind", "concrete")
    kind = kind.strip().lower() if isinstance(kind, str) else "concrete"
    if kind not in VALID_KINDS:
        kind = "concrete"
    sentence = item.get("sentence", 0)
    try:
        sentence = int(sentence)
    except (TypeError, ValueError):
        sentence = 0
    sentence = max(0, min(sentence, n_sentences - 1)) if n_sentences > 0 else 0
    return Entity(name=name.strip(), kind=kind, sentence=sentence)


def _parse_response(response_text: str, sentences: list[str]) -> EntityResult:
    data = json.loads(response_text)
    type_ = data.get("type", "abstract")
    n_sentences = len(sentences)

    if type_ == "quote":
        return EntityResult(
            type="quote",
            text=str(data.get("text", "")).strip().strip('"'),
            attribution=str(data.get("attribution", "")).strip(),
            sentences=sentences,
        )

    if type_ == "entities":
        raw_entities = data.get("entities", [])
        entities: list[Entity] = []
        if isinstance(raw_entities, list):
            for item in raw_entities:
                e = _coerce_entity(item, n_sentences)
                if e is not None:
                    entities.append(e)
        if not entities:
            return EntityResult(type="abstract", sentences=sentences)
        return EntityResult(type="entities", entities=entities, sentences=sentences)

    return EntityResult(type="abstract", sentences=sentences)


def extract(paragraph_text: str) -> EntityResult:
    """Extract sentence-tagged entities, quote, or abstract classification."""
    sentences = split_sentences(paragraph_text)
    if not sentences:
        return EntityResult(type="abstract")

    numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences))
    prompt = PROMPT.format(paragraph=paragraph_text, numbered_sentences=numbered)

    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response_text = _call_ollama(prompt)
            return _parse_response(response_text, sentences)
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(f"Entity extraction attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

    logger.warning("Entity extraction failed, treating as abstract")
    return EntityResult(type="abstract", sentences=sentences)


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
        # Group entities by sentence for readable output
        by_sentence: dict[int, list[Entity]] = {}
        for e in result.entities:
            by_sentence.setdefault(e.sentence, []).append(e)
        for i, sent in enumerate(result.sentences):
            preview = sent[:80] + ("…" if len(sent) > 80 else "")
            ents = by_sentence.get(i, [])
            ent_str = "  ".join(f"[{e.kind:8s}] {e.name}" for e in ents)
            print(f"  S{i}: {preview}")
            print(f"      → {ent_str if ent_str else '(none)'}")
