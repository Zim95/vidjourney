"""
LLM-driven title extraction for paragraph-anchored list scenes.

Returns a SHORT (3-6 word) noun phrase that captions a bullet stack on screen,
matching the contract that `llm_listify` already produces for listified
paragraphs. The title appears above the bullet stack on every page; on page 2+
of a paginated list the caller appends " (contd...)" so the viewer always
knows what the bullets enumerate.

Failure mode: returns ``""`` — `_build_list_scene` treats empty as "no title"
and skips emitting `SHOW_LIST_TITLE`, preserving the previous look.
"""
import json
import logging

import requests

from src.config.constants import (
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
)

logger = logging.getLogger(__name__)


PROMPT = """\
Given an intro paragraph and the bullet items that follow it, produce a SHORT
noun phrase (3-6 words) that captions what the bullets enumerate. The title is
shown on screen as a header above the bullet stack; it is NOT narrated.

Intro paragraph:
"{anchor}"

Bullet items:
{items}

Output JSON only:
{{"title": "<3-6 word noun phrase>"}}

Rules:
- 3 to 6 words.
- NO punctuation, NO trailing period.
- NO leading numeric count word ("Three", "Four", etc.) — counts in titles
  routinely disagree with the actual bullet count.
- A noun phrase — names what the bullets are, e.g. "Core concerns",
  "Software reliability expectations", "Common application needs",
  "Types of systematic faults".
- Stay grounded in the paragraph — don't invent terminology.

Examples:
- intro: "For software, typical expectations include:"
  items: ["Application meets user expectations", "Tolerates user mistakes",
          "Performance meets requirements", "Prevents unauthorized access"]
  → {{"title": "Software reliability expectations"}}
- intro: "For example, many applications need to:"
  items: ["Store data for later access", "Remember results to speed up reads",
          "Search data by keyword filter"]
  → {{"title": "Common application needs"}}"""


_COUNT_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
}


def _strip_leading_count(title: str) -> str:
    parts = title.split(maxsplit=1)
    if len(parts) > 1 and parts[0].lower() in _COUNT_WORDS:
        return parts[1]
    return title


def _call_ollama(prompt: str) -> str:
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


def extract_list_title(anchor_text: str, item_summaries: list[str]) -> str:
    """Return a 3-6 word title for the bullet stack, or ``""`` on failure.

    ``item_summaries`` are the short on-screen labels (the same strings the
    bullets display). Passing them grounds the LLM so the title matches what's
    actually enumerated.
    """
    if not (anchor_text and anchor_text.strip()) or not item_summaries:
        return ""

    items_block = "\n".join(f"- {s}" for s in item_summaries)
    prompt = PROMPT.format(anchor=anchor_text.strip(), items=items_block)

    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response_text = _call_ollama(prompt)
            data = json.loads(response_text)
            raw = data.get("title", "")
            if not isinstance(raw, str):
                continue
            title = raw.strip().rstrip(".,;:!?").strip()
            title = _strip_leading_count(title)
            words = title.split()
            if 2 <= len(words) <= 8 and title:
                return title
            logger.warning(f"List title length out of range: {title!r}")
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(f"list-title attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

    return ""


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="LLM-based list title extractor")
    parser.add_argument("--anchor", required=True, help="Intro paragraph text")
    parser.add_argument("--items", required=True, nargs="+", help="Bullet summaries")
    args = parser.parse_args()

    title = extract_list_title(args.anchor, args.items)
    if title:
        print(title)
    else:
        print("(no title)", file=sys.stderr)
        sys.exit(1)
