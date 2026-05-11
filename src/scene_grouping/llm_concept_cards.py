"""
LLM-based concept-card extractor.

For paragraphs that don't listify (genuine prose — narrative explanations,
causal chains, conceptual overviews), we pick 2-4 KEY MOMENTS in the paragraph
and present each as a full-frame card with a short title + a 1-2 sentence body.

Why not entity icons / pills? Spamming an icon per noun every sentence creates
visual noise without teaching anything. Concept cards do the opposite: pick a
few important beats, give each one space and dwell time, and let the eye rest.

The cards' `text` (full sentences) are TTS-narrated in sequence; the `title`
and `body` are display-only. Cards transition one after another, in the order
they appear in the paragraph. Together the cards' texts cover the whole
paragraph — nothing is dropped.

Used by `_build_paragraph_blank_scenes` in llm_timeline.py as the default
visual treatment when listify doesn't fire and the paragraph isn't a quote.

Usage:
    from src.scene_grouping.llm_concept_cards import extract_cards
    result = extract_cards(paragraph_text)
    for card in result.cards:
        print(card.title, card.body, card.text)
"""
import json
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
You are designing a sequence of 2-4 full-frame display cards for a paragraph
of educational text. Each card stays on screen during a portion of the audio
narration; the next card replaces it for the next portion.

Paragraph:
"{paragraph}"

Reply with valid JSON only.

Goal: pick 2 to 4 KEY MOMENTS in the paragraph. Each moment becomes one card.
A "moment" is a coherent beat — usually 2-4 sentences that share a single
focus (one idea, one transition, one comparison). Do NOT make a card per
sentence — that's too noisy. Do NOT make a single card for the whole
paragraph — that defeats the point.

Output schema:
{{
  "cards": [
    {{"text": "<exact consecutive sentences from the paragraph for this card>",
      "title": "<3-6 word headline that names the beat>",
      "body":  "<1-2 short sentences (max ~20 words total) summarizing this beat for display>"}},
    ...
  ]
}}

Strict rules:
- 2 to 4 cards. NEVER 1 (use the fallback path) and never 5+ (too many).
- Cards are SEQUENTIAL — they appear in the same order as in the paragraph.
- Each `text` is verbatim consecutive sentences from the paragraph (used as TTS).
- Together, the `text` fields must cover the WHOLE paragraph in order. No
  drops, no rewrites.
- `title` is a SHORT noun phrase (3-6 words, no punctuation, no trailing period).
- `body` is a tight 1-2 sentence summary for display (max ~20 words). It
  should NOT just repeat `text`; it should say "what to take away from this
  beat". Sentence case, ends with a period.
- Don't introduce concepts that aren't in the paragraph."""


@dataclass
class ConceptCard:
    """One card in the sequence. `.text` is narration; `.title` + `.body` display."""
    text: str
    title: str
    body: str


@dataclass
class ConceptCardsResult:
    cards: list[ConceptCard] = field(default_factory=list)

    def is_valid(self) -> bool:
        return 2 <= len(self.cards) <= 4 and all(
            c.text and c.title and c.body for c in self.cards
        )


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


def _coerce_card(raw) -> ConceptCard | None:
    if not isinstance(raw, dict):
        return None
    text = raw.get("text", "")
    title = raw.get("title", "")
    body = raw.get("body", "")
    if not all(isinstance(s, str) and s.strip() for s in (text, title, body)):
        return None
    return ConceptCard(
        text=text.strip(),
        title=title.strip().rstrip(".,;:!?").strip(),
        body=body.strip(),
    )


def _parse_response(response_text: str) -> ConceptCardsResult:
    data = json.loads(response_text)
    raw_cards = data.get("cards", [])
    cards: list[ConceptCard] = []
    if isinstance(raw_cards, list):
        for raw in raw_cards:
            card = _coerce_card(raw)
            if card is not None:
                cards.append(card)
    return ConceptCardsResult(cards=cards[:4])  # hard cap at 4


def extract_cards(paragraph_text: str) -> ConceptCardsResult:
    """Ask the LLM to break `paragraph_text` into 2-4 sequential concept cards.

    Returns ConceptCardsResult; check `.is_valid()` before using. On parse
    failure, returns an empty result and the caller falls back accordingly.
    """
    text = (paragraph_text or "").strip()
    if not text:
        return ConceptCardsResult()

    prompt = PROMPT.format(paragraph=text)

    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response_text = _call_ollama(prompt)
            result = _parse_response(response_text)
            if result.is_valid():
                logger.info(f"Concept cards: {len(result.cards)} cards extracted")
                return result
            logger.warning(
                f"Concept cards attempt {attempt}/{OLLAMA_MAX_RETRIES} returned "
                f"invalid result ({len(result.cards)} cards)"
            )
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(f"Concept cards attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

    logger.warning("Concept cards extraction failed; returning empty")
    return ConceptCardsResult()


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="LLM-based concept-card extractor")
    parser.add_argument("text", type=str, nargs="?")
    parser.add_argument("--file", type=str)
    args = parser.parse_args()

    if args.file:
        paragraph = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        paragraph = args.text
    else:
        parser.print_help()
        sys.exit(1)

    result = extract_cards(paragraph)
    if result.is_valid():
        print(f"{len(result.cards)} cards:")
        for i, card in enumerate(result.cards, 1):
            print(f"  [{i}] title={card.title!r}")
            print(f"      body={card.body!r}")
            print(f"      text={card.text[:120]}{'…' if len(card.text) > 120 else ''}")
    else:
        print(f"INVALID ({len(result.cards)} cards)")
