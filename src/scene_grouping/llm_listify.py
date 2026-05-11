"""
LLM-based listification detector.

Some prose paragraphs hide a list inside them — sequential questions, named
concerns with definitions ("Reliability... Scalability... Maintainability..."),
or "First X. Second Y. Third Z." structures. These read better as a bulleted
list than as a stream of entity icons.

This module asks the LLM whether a paragraph contains an implicit enumeration
that should be visualized as bullets, and if so, returns the intro prose,
the bullet items (full text + short summary), and any trailing prose.

Used by `_build_paragraph_blank_scenes` in llm_timeline.py — listification is
tried first; if it fires, the scene routes through the existing list-scene
flow (intro + accumulating bullets + parts.json + smoothed subtitles). If
not, the entity-extraction path takes over.

Usage:
    from src.scene_grouping.llm_listify import listify
    result = listify(paragraph_text)
    if result.should_listify():
        # use result.intro, result.items, result.outro
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
You are analyzing a paragraph from an educational text to decide whether it
contains an IMPLICIT enumeration that would read better as a bulleted list
than as continuous prose.

Paragraph:
"{paragraph}"

Reply with valid JSON only.

Listify ONLY when the enumeration is the FOCUS of the paragraph and has at
least 3 parallel items with clear structure. Specifically, listify when the
paragraph contains ONE of:
- Three or more consecutive questions ("How do you...? How do you...? How do you...?")
- An explicit count followed by 3+ named items ("three concerns: Reliability... Scalability... Maintainability...")
- Numbered or sequential structure with 3+ steps ("First X. Second Y. Third Z.")
- A definition list embedded in prose with 3+ terms (Term: explanation. Term: explanation. Term: explanation.)

Do NOT listify when:
- The paragraph is normal prose, even if it mentions multiple things in passing
  (e.g. "we use databases, queues, and caches" — this is a passing mention,
  not a focused enumeration)
- The paragraph is a journey / overview / motivation / narrative
  (e.g. "This book is a journey through... we will explore..." — narrative,
  even if it lists topics it covers)
- There are fewer than 3 distinct, parallel items
- Items would be paraphrased into something not actually present in the
  paragraph as named entities (if you have to invent the item names, don't listify)
- The list spans the whole paragraph already as line-broken bullets
- The paragraph is a quotation or single argument

When in doubt, return {{"listify": false}} — concept cards (the default
fallback) handle prose better than forced bullets do.

Output schema if listification applies:
{{
  "listify": true,
  "title": "<3-6 word noun phrase naming what these bullets enumerate>",
  "intro": "<sentences before the first list item, may be empty string>",
  "items": [
    {{"text": "<full sentence(s) for this bullet, narrated verbatim>",
      "summary": "<3-5 word phrase suitable for on-screen bullet>"}},
    ...
  ],
  "outro": "<any trailing sentences AFTER the last list item, may be empty>"
}}

Otherwise:
{{"listify": false}}

Strict rules:
- `items` must have at least 3 entries when listify=true.
- INCLUDE EVERY PARALLEL ITEM. If the paragraph repeats the same structure
  for several items (e.g. "Reliability ... <description>. Scalability ...
  <description>. Maintainability ... <description>."), your `items` array
  MUST contain ALL of them — count the parallel terms in the source and
  produce that many items. Do not omit one because it seems redundant.
- `title` is a SHORT noun phrase (3-6 words, NO punctuation, NO trailing period)
  that captions the bullet list — e.g., "Core concerns", "Tricky design
  questions", "Steps to deploy", "System fault types". It is display-only and
  shown as a header above the bullets; it is NOT narrated.
- DO NOT include a numeric count word ("Three", "Four", "Two", etc.) in the
  title. Counts in titles often disagree with the actual items array. Use
  "Core concerns" rather than "Three core concerns".
- Each item's `text` must be a substring or very close paraphrase of the
  paragraph (used directly as TTS narration).
- Each item's `summary` is a SHORT 3-5 word phrase (NO punctuation, NO
  trailing period). It is what appears as the bullet on screen. When the
  paragraph names the item (e.g., "Reliability ... Scalability ..."), USE
  THAT NAME as the summary verbatim. Don't paraphrase it into a description.
- `intro` and `outro` are taken verbatim from the paragraph; do not paraphrase.
- Together, intro + items[].text + outro must roughly cover the paragraph
  (minor whitespace/connector-word changes are fine).
- If the paragraph clearly listifies, RETURN listify=true. Only fall back to
  {{"listify": false}} when there really is no enumeration."""


@dataclass
class ListifyItem:
    """One bullet point. Has `.text` (narration) and `.summary` (display) so
    it duck-types as the Element shape that `_build_list_scene` expects."""
    text: str
    summary: str


@dataclass
class ListifyResult:
    listify: bool = False
    title: str = ""
    intro: str = ""
    items: list[ListifyItem] = field(default_factory=list)
    outro: str = ""

    def should_listify(self) -> bool:
        """Whether the paragraph should actually be rendered as a list scene."""
        return self.listify and len(self.items) >= 3


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


# Number words that frequently appear in titles and end up disagreeing with
# the actual items count. Stripped from titles defensively (case-insensitive)
# so we never display "Three concerns" while only showing two bullets.
_COUNT_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
}


def _strip_leading_count(title: str) -> str:
    """Remove a leading number word from `title` if present.

    "Three core concerns"  → "Core concerns"
    "Four tricky questions" → "Tricky questions"
    Leaves untouched if there's no leading count word.
    """
    parts = title.split(maxsplit=1)
    if not parts:
        return title
    if parts[0].lower() in _COUNT_WORDS and len(parts) == 2:
        return parts[1][:1].upper() + parts[1][1:]  # capitalize the new first word
    return title


def _coerce_item(raw) -> ListifyItem | None:
    if not isinstance(raw, dict):
        return None
    text = raw.get("text", "")
    summary = raw.get("summary", "")
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(summary, str) or not summary.strip():
        return None
    # Trim trailing punctuation from summaries — they're display-only.
    summary = summary.strip().rstrip(".,;:!?").strip()
    if not summary:
        return None
    return ListifyItem(text=text.strip(), summary=summary)


def _parse_response(response_text: str) -> ListifyResult:
    data = json.loads(response_text)
    if not data.get("listify"):
        return ListifyResult(listify=False)

    raw_items = data.get("items", [])
    items: list[ListifyItem] = []
    if isinstance(raw_items, list):
        for raw in raw_items:
            item = _coerce_item(raw)
            if item is not None:
                items.append(item)

    if len(items) < 3:
        # LLM said listify=true but didn't deliver enough items — discard.
        # Below 3 items it's almost always a forced enumeration of prose.
        return ListifyResult(listify=False)

    title = data.get("title", "")
    intro = data.get("intro", "")
    outro = data.get("outro", "")
    title_str = str(title).strip().rstrip(".,;:!?").strip() if isinstance(title, str) else ""
    # Defensive: strip leading count words so titles never disagree with the
    # actual items count (LLM occasionally produces "Three concerns" with 2 items).
    title_str = _strip_leading_count(title_str)
    return ListifyResult(
        listify=True,
        title=title_str,
        intro=str(intro).strip() if isinstance(intro, str) else "",
        items=items,
        outro=str(outro).strip() if isinstance(outro, str) else "",
    )


def listify(paragraph_text: str) -> ListifyResult:
    """Ask the LLM whether `paragraph_text` should be visualized as a bulleted
    list. Returns ListifyResult — call `.should_listify()` to gate behavior."""
    text = (paragraph_text or "").strip()
    if not text:
        return ListifyResult(listify=False)

    prompt = PROMPT.format(paragraph=text)

    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response_text = _call_ollama(prompt)
            result = _parse_response(response_text)
            if result.should_listify():
                logger.info(f"Listified paragraph into {len(result.items)} bullets")
            return result
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(f"Listify attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

    logger.warning("Listify failed, falling back to non-listified")
    return ListifyResult(listify=False)


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="LLM-based listification detector")
    parser.add_argument("text", type=str, nargs="?", help="Paragraph text to analyze")
    parser.add_argument("--file", type=str, help="Read paragraph from a file")
    args = parser.parse_args()

    if args.file:
        paragraph = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        paragraph = args.text
    else:
        parser.print_help()
        sys.exit(1)

    result = listify(paragraph)
    if result.should_listify():
        print(f"LISTIFIED ({len(result.items)} items)")
        if result.title:
            print(f"  title: {result.title!r}")
        if result.intro:
            print(f"  intro: {result.intro[:100]}…" if len(result.intro) > 100 else f"  intro: {result.intro}")
        for i, it in enumerate(result.items, 1):
            print(f"  [{i}] summary={it.summary!r}")
            print(f"      text={it.text[:120]}…" if len(it.text) > 120 else f"      text={it.text}")
        if result.outro:
            print(f"  outro: {result.outro[:100]}…" if len(result.outro) > 100 else f"  outro: {result.outro}")
    else:
        print("NOT LISTIFIED")
