"""
LLM-based element grouper.

Parses a section file into elements, then uses an LLM to determine which
paragraphs are associated with which resources (IMAGE, CODE_BLOCK, TABLE).

Structural rules handle the rest:
- HEADING → always standalone
- CAPTION → always attaches to preceding IMAGE
- LIST_ITEM → attaches to preceding PARAGRAPH
- LINK / ANNOTATION / DRAWING → skipped (metadata)

Usage:
    python -m src.scene_grouping.llm_grouper pipeline/sections/section_3.txt
    python -m src.scene_grouping.llm_grouper --all
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests

from src.utils import logger, timer
from src.config.constants import (
    GROUPING_SECTIONS_DIR,
    GROUPING_CONTENT_GROUPS_DIR,
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
)
from src.ingestion.quote_attribution import is_quote, split_quote_and_prose


SECTIONS_DIR = GROUPING_SECTIONS_DIR

SKIP_KINDS = {"LINK", "ANNOTATION", "DRAWING"}
RESOURCE_KINDS = {"IMAGE", "CODE_BLOCK", "TABLE"}

PROMPT = """\
You are given a numbered list of elements extracted from a document section.

Your task: for each PARAGRAPH, determine which resources it is associated with.
Resources are elements of kind IMAGE, CODE_BLOCK, or TABLE — identified by their index number.

A PARAGRAPH is "associated" with a resource if:
- It introduces the resource (e.g., "consider the following code:", "as shown in Figure 1-1")
- It explains the resource (e.g., "This query returns...", "The diagram above shows...")
- It describes or references the resource in any way

Important:
- Only associate PARAGRAPHs with IMAGE, CODE_BLOCK, or TABLE elements. Never with HEADING or CAPTION.
- A PARAGRAPH can be associated with zero or more resources.
- A resource can be associated with at most one PARAGRAPH.
- If a PARAGRAPH does not reference any resource, give it an empty list.
- Include ALL PARAGRAPH indices in your output.

Additionally, flag paragraphs that contain an inline numbered or bulleted list of 2+ items
(e.g. "... needed functionality. For example, many applications need to: 1. First item. 2. Second item. 3. Third item.").
Do NOT flag prose that merely mentions numbers (e.g. "In the 1970s...").
Return these as a list of paragraph indices under "lists".

Output valid JSON only:
{{"associations": {{"<paragraph_index>": [<resource_indices>], ...}}, "lists": [<paragraph_index>, ...]}}

Example input:
[0] HEADING Introduction
[1] PARAGRAPH This chapter introduces key concepts. Consider the following diagram:
[2] IMAGE path/to/diagram.png
[3] CAPTION Figure 1. System overview.
[4] PARAGRAPH The system has three main components.
[5] PARAGRAPH The next four chapters go through key topics: 1. Chapter 1 introduces terminology. 2. Chapter 2 compares data models. 3. Chapter 3 covers storage engines.

Example output:
{{"associations": {{"1": [2], "4": [], "5": []}}, "lists": [5]}}

Now analyze these elements:
{numbered_elements}"""


# --- Data structures ---

@dataclass
class Element:
    kind: str       # HEADING, PARAGRAPH, LIST_ITEM, IMAGE, TABLE, CAPTION, LINK, etc.
    text: str       # content (path for IMAGE/TABLE/CODE_BLOCK)
    summary: str | None = None  # short bullet-friendly version (LIST_ITEM only)


@dataclass
class ContentGroup:
    kind: str               # "heading" | "paragraph" | "quote" | "image" | "code_block" | "table" | "list"
    elements: list[Element] # anchor first, then absorbed resources/captions/list_items

    @property
    def anchor(self) -> Element:
        return self.elements[0]

    @property
    def resources(self) -> list[Element]:
        return [e for e in self.elements if e.kind in RESOURCE_KINDS]

    @property
    def captions(self) -> list[Element]:
        return [e for e in self.elements if e.kind == "CAPTION"]

    @property
    def list_items(self) -> list[Element]:
        return [e for e in self.elements if e.kind == "LIST_ITEM"]

    @property
    def caption_for_resource(self) -> dict[str, str | None]:
        """Map resource path → caption text (or None)."""
        result: dict[str, str | None] = {}
        for i, e in enumerate(self.elements):
            if e.kind in RESOURCE_KINDS:
                cap = None
                if i + 1 < len(self.elements) and self.elements[i + 1].kind == "CAPTION":
                    cap = self.elements[i + 1].text
                result[e.text] = cap
        return result


# --- Serialization ---

def serialize_groups(groups: list[ContentGroup]) -> str:
    """Serialize content groups to a human-readable text format.

    Format:
        GROUP 0: heading
          HEADING Thinking About Data Systems

        GROUP 1: paragraph
          PARAGRAPH The first four chapters...
          LIST_ITEM Chapter 1 introduces the terminology...
          SUMMARY: Terminology & approach
    """
    blocks = []
    for i, group in enumerate(groups):
        lines = [f"GROUP {i}: {group.kind}"]
        for el in group.elements:
            lines.append(f"  {el.kind} {el.text}")
            if el.summary:
                lines.append(f"  SUMMARY: {el.summary}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def deserialize_groups(text: str) -> list[ContentGroup]:
    """Deserialize content groups from the text format."""
    groups: list[ContentGroup] = []
    current_kind: str | None = None
    current_elements: list[Element] = []

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        # GROUP header: "GROUP 0: heading"
        group_match = re.match(r"^GROUP\s+\d+:\s+(\S+)$", line)
        if group_match:
            if current_kind is not None:
                groups.append(ContentGroup(kind=current_kind, elements=current_elements))
            current_kind = group_match.group(1)
            current_elements = []
            continue

        # Summary metadata for previous element: "SUMMARY: text"
        summary_match = re.match(r"^SUMMARY:\s*(.*)$", line)
        if summary_match and current_elements:
            current_elements[-1].summary = summary_match.group(1).strip()
            continue

        # Element line: "  KIND text..."
        el_match = re.match(
            r"^(HEADING|PARAGRAPH|QUOTE|LIST_ITEM|IMAGE|TABLE|CAPTION|CODE_BLOCK)\s+(.*)$",
            line,
        )
        if el_match and current_kind is not None:
            current_elements.append(Element(kind=el_match.group(1), text=el_match.group(2).strip()))
            continue

        # Continuation line — append to previous text element
        if current_elements and current_elements[-1].kind in ("PARAGRAPH", "QUOTE", "LIST_ITEM", "HEADING", "CAPTION"):
            current_elements[-1].text += " " + line

    if current_kind is not None:
        groups.append(ContentGroup(kind=current_kind, elements=current_elements))

    return groups


# --- Section parsing ---

def parse_section(content: str) -> list[Element]:
    """Parse a section file into a flat list of elements."""
    elements: list[Element] = []

    for raw_line in content.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("section_number:") or line.startswith("page_number:"):
            continue

        match = re.match(
            r"^(HEADING|PARAGRAPH|QUOTE|LIST_ITEM|IMAGE|TABLE|CAPTION|LINK|ANNOTATION|CODE_BLOCK)\s+(.*)$",
            line,
        )
        if not match:
            if elements and elements[-1].kind in ("PARAGRAPH", "QUOTE", "LIST_ITEM", "HEADING", "CAPTION"):
                elements[-1].text += " " + line
            continue

        kind = match.group(1)
        text = match.group(2).strip()
        elements.append(Element(kind=kind, text=text))

    # Safety net: in section files that pre-date ingestion-level QUOTE
    # emission, a quote may still arrive as a PARAGRAPH (possibly merged with
    # the following paragraph because PDF extraction lost the blank line).
    # Re-apply the same split + type-tag logic here so older artifacts get the
    # quote treatment without needing a full re-ingest.
    expanded: list[Element] = []
    for el in elements:
        if el.kind != "PARAGRAPH":
            expanded.append(el)
            continue
        split = split_quote_and_prose(el.text)
        if split is not None:
            quote_text, prose_text = split
            expanded.append(Element(kind="QUOTE", text=quote_text))
            expanded.append(Element(kind="PARAGRAPH", text=prose_text))
            continue
        if is_quote(el.text):
            expanded.append(Element(kind="QUOTE", text=el.text))
            continue
        expanded.append(el)
    return expanded


# --- LLM call ---

def _build_prompt(elements: list[Element]) -> str:
    """Build the LLM prompt with numbered elements."""
    lines = []
    for i, el in enumerate(elements):
        lines.append(f"[{i}] {el.kind} {el.text}")
    numbered = "\n".join(lines)
    return PROMPT.format(numbered_elements=numbered)


def _call_ollama(prompt: str) -> str:
    """Call Ollama chat API and return the response text."""
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": 16384, "temperature": 0},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _parse_llm_response(
    response_text: str,
    elements: list[Element],
) -> tuple[dict[int, list[int]], set[int]]:
    """Parse LLM JSON response into (associations, list_paragraph_indices).

    associations: {paragraph_index: [resource_indices]}
    list_paragraph_indices: {paragraph_index, ...} — paragraphs that contain embedded lists
    """
    data = json.loads(response_text)

    associations_raw = data.get("associations", {})
    if not isinstance(associations_raw, dict):
        associations_raw = {}

    associations: dict[int, list[int]] = {}
    for key, value in associations_raw.items():
        try:
            para_idx = int(key)
        except (TypeError, ValueError):
            continue
        if para_idx < 0 or para_idx >= len(elements):
            continue
        if elements[para_idx].kind != "PARAGRAPH":
            continue
        resource_indices = []
        if isinstance(value, list):
            for r_idx in value:
                try:
                    r_idx_int = int(r_idx)
                except (TypeError, ValueError):
                    continue
                if 0 <= r_idx_int < len(elements) and elements[r_idx_int].kind in RESOURCE_KINDS:
                    resource_indices.append(r_idx_int)
        associations[para_idx] = resource_indices

    lists_raw = data.get("lists", [])
    list_indices: set[int] = set()
    if isinstance(lists_raw, list):
        for idx in lists_raw:
            try:
                p = int(idx)
            except (TypeError, ValueError):
                continue
            if 0 <= p < len(elements) and elements[p].kind == "PARAGRAPH":
                list_indices.add(p)

    return associations, list_indices


# --- Embedded list splitting (regex) ---

# Matches numbered markers at start of segment: "1. ", "2) ", "(3) ", "a. ", etc.
_LIST_MARKER_RE = re.compile(
    r"(?:^|(?<=\s))"                    # start or after whitespace
    r"(?:\(?(?:\d{1,2}|[a-z]|[ivx]{1,4}))\)"  # "1)" / "(1)" / "a)" / "iv)"
    r"|"
    r"(?:^|(?<=\s))"
    r"(?:\(?(?:\d{1,2}|[a-z]|[ivx]{1,4}))\.\s"  # "1. " / "a. " / "iv. "
    r"|"
    r"(?:^|(?<=\s))[•·*]\s"                     # "• " / "· " / "* "
)


def _split_embedded_list(paragraph_text: str) -> tuple[str, list[str]] | None:
    """Split a paragraph into (intro, [items]) if it contains an inline list.

    Returns None if the text doesn't look like a list (fewer than 2 markers found).
    """
    # Find all marker positions
    matches = list(_LIST_MARKER_RE.finditer(paragraph_text))
    if len(matches) < 2:
        return None

    intro = paragraph_text[: matches[0].start()].strip()
    items: list[str] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(paragraph_text)
        item = paragraph_text[start:end].strip()
        if item:
            items.append(item)

    if len(items) < 2:
        return None
    return intro, items


# --- Structural fallback ---

def _structural_fallback(elements: list[Element]) -> list[ContentGroup]:
    """Forward-scan grouping without LLM. Used when LLM is unavailable or fails."""
    groups: list[ContentGroup] = []
    i = 0

    while i < len(elements):
        el = elements[i]

        if el.kind in SKIP_KINDS:
            i += 1
            continue

        if el.kind == "HEADING":
            groups.append(ContentGroup(kind="heading", elements=[el]))
            i += 1
            continue

        if el.kind == "QUOTE":
            # Quotes are always standalone — they don't absorb resources,
            # list items, or captions. The full quote text + attribution
            # lives in el.text.
            groups.append(ContentGroup(kind="quote", elements=[el]))
            i += 1
            continue

        if el.kind == "PARAGRAPH":
            group_els = [el]
            j = i + 1
            while j < len(elements):
                nxt = elements[j]
                if nxt.kind in ("PARAGRAPH", "QUOTE", "HEADING"):
                    break
                if nxt.kind in SKIP_KINDS:
                    j += 1
                    continue
                if nxt.kind in RESOURCE_KINDS or nxt.kind in ("CAPTION", "LIST_ITEM"):
                    group_els.append(nxt)
                    j += 1
                    continue
                j += 1
            groups.append(ContentGroup(kind="paragraph", elements=group_els))
            i = j
            continue

        if el.kind == "CAPTION":
            if groups and any(e.kind in RESOURCE_KINDS for e in groups[-1].elements):
                groups[-1].elements.append(el)
            i += 1
            continue

        if el.kind in RESOURCE_KINDS:
            kind_map = {"IMAGE": "image", "CODE_BLOCK": "code_block", "TABLE": "table"}
            group_els = [el]
            j = i + 1
            while j < len(elements) and elements[j].kind == "CAPTION":
                group_els.append(elements[j])
                j += 1
            groups.append(ContentGroup(kind=kind_map[el.kind], elements=group_els))
            i = j
            continue

        if el.kind == "LIST_ITEM":
            group_els = [el]
            j = i + 1
            while j < len(elements) and elements[j].kind == "LIST_ITEM":
                group_els.append(elements[j])
                j += 1
            groups.append(ContentGroup(kind="list", elements=group_els))
            i = j
            continue

        i += 1

    return groups


# --- LLM-based grouping ---

def _apply_embedded_lists(
    elements: list[Element],
    associations: dict[int, list[int]],
    list_indices: set[int],
) -> tuple[list[Element], dict[int, list[int]]]:
    """Rewrite elements by splitting paragraphs flagged as containing lists.

    For each flagged paragraph, apply the regex splitter. If it finds items:
    - Replace its text with the intro
    - Insert LIST_ITEM elements immediately after it
    - Remap all indices in associations to account for the insertions
    """
    if not list_indices:
        return elements, associations

    new_elements: list[Element] = []
    index_map: dict[int, int] = {}

    for old_idx, el in enumerate(elements):
        index_map[old_idx] = len(new_elements)

        if el.kind == "PARAGRAPH" and old_idx in list_indices:
            split = _split_embedded_list(el.text)
            if split is not None:
                intro, items = split
                new_elements.append(Element(kind="PARAGRAPH", text=intro or el.text))
                for item_text in items:
                    new_elements.append(Element(kind="LIST_ITEM", text=item_text))
                logger.info(f"Split paragraph {old_idx} into intro + {len(items)} list items")
                continue

        new_elements.append(el)

    new_associations: dict[int, list[int]] = {}
    for old_para_idx, resource_indices in associations.items():
        new_para_idx = index_map.get(old_para_idx)
        if new_para_idx is None:
            continue
        new_resources = [index_map[r] for r in resource_indices if r in index_map]
        new_associations[new_para_idx] = new_resources

    return new_elements, new_associations


def _assemble_groups(elements: list[Element], associations: dict[int, list[int]]) -> list[ContentGroup]:
    """Build ContentGroup list from elements and LLM-provided associations."""
    groups: list[ContentGroup] = []

    # Track which resource indices are claimed by a paragraph
    claimed_resources: set[int] = set()
    for resource_indices in associations.values():
        claimed_resources.update(resource_indices)

    # Track which elements have been consumed
    consumed: set[int] = set()

    i = 0
    while i < len(elements):
        if i in consumed:
            i += 1
            continue

        el = elements[i]

        if el.kind in SKIP_KINDS:
            i += 1
            continue

        if el.kind == "HEADING":
            groups.append(ContentGroup(kind="heading", elements=[el]))
            consumed.add(i)
            i += 1
            continue

        if el.kind == "QUOTE":
            # Quotes are always standalone (see structural fallback).
            groups.append(ContentGroup(kind="quote", elements=[el]))
            consumed.add(i)
            i += 1
            continue

        if el.kind == "PARAGRAPH":
            group_els = [el]
            consumed.add(i)

            # Add LLM-associated resources (and their captions)
            for r_idx in associations.get(i, []):
                if r_idx not in consumed:
                    group_els.append(elements[r_idx])
                    consumed.add(r_idx)
                    # Check for caption immediately after the resource
                    cap_idx = r_idx + 1
                    if (cap_idx < len(elements)
                            and elements[cap_idx].kind == "CAPTION"
                            and cap_idx not in consumed):
                        group_els.append(elements[cap_idx])
                        consumed.add(cap_idx)

            # Greedily absorb consecutive LIST_ITEMs that follow this paragraph
            j = i + 1
            while j < len(elements):
                if elements[j].kind == "LIST_ITEM" and j not in consumed:
                    group_els.append(elements[j])
                    consumed.add(j)
                    j += 1
                elif elements[j].kind in SKIP_KINDS:
                    j += 1
                elif elements[j].kind == "CAPTION" and j not in consumed:
                    # Caption between paragraph and list items — attach if group has a resource
                    if any(e.kind in RESOURCE_KINDS for e in group_els):
                        group_els.append(elements[j])
                        consumed.add(j)
                    j += 1
                elif elements[j].kind in RESOURCE_KINDS and j in claimed_resources:
                    # This resource is claimed — skip past it (will be picked up by its paragraph)
                    j += 1
                else:
                    break
            groups.append(ContentGroup(kind="paragraph", elements=group_els))
            i += 1
            continue

        if el.kind == "CAPTION":
            # Orphaned caption — attach to last group if it has a resource
            if groups and any(e.kind in RESOURCE_KINDS for e in groups[-1].elements):
                groups[-1].elements.append(el)
            consumed.add(i)
            i += 1
            continue

        if el.kind in RESOURCE_KINDS:
            # Unclaimed standalone resource
            if i in claimed_resources:
                # Will be consumed by its associated paragraph — skip
                i += 1
                continue
            kind_map = {"IMAGE": "image", "CODE_BLOCK": "code_block", "TABLE": "table"}
            group_els = [el]
            consumed.add(i)
            # Absorb following captions
            j = i + 1
            while j < len(elements) and elements[j].kind == "CAPTION" and j not in consumed:
                group_els.append(elements[j])
                consumed.add(j)
                j += 1
            groups.append(ContentGroup(kind=kind_map[el.kind], elements=group_els))
            i += 1
            continue

        if el.kind == "LIST_ITEM":
            # Standalone list items (not preceded by a paragraph)
            group_els = [el]
            consumed.add(i)
            j = i + 1
            while j < len(elements) and elements[j].kind == "LIST_ITEM" and j not in consumed:
                group_els.append(elements[j])
                consumed.add(j)
                j += 1
            groups.append(ContentGroup(kind="list", elements=group_els))
            i += 1
            continue

        i += 1

    return groups


def _merge_adjacent_resources(groups: list[ContentGroup]) -> list[ContentGroup]:
    """
    Structural post-pass: merge standalone resources into adjacent paragraph groups.

    If the LLM missed an association, a resource ends up standalone even though
    the paragraph right before it says something like "For example:" — this pass
    catches those by merging a standalone resource into the preceding paragraph group.
    """
    merged: list[ContentGroup] = []

    for group in groups:
        if (group.kind in ("image", "code_block", "table")
                and merged
                and merged[-1].kind == "paragraph"):
            # Merge this standalone resource into the preceding paragraph group
            merged[-1].elements.extend(group.elements)
            logger.info(f"Post-pass: merged standalone {group.kind} into preceding paragraph group")
        else:
            merged.append(group)

    return merged


SUMMARY_PROMPT = """\
You are given a list of bullet items from a document. For each item, write a
SHORT on-screen bullet in the voice of a reader taking notes — preserving the
concrete content, not abstracting it away.

Style:
- Write the way you'd jot a note in a notebook while reading. Capture the
  specific facts the author chose to include. Numbers, names, comparisons,
  and key relationships MUST survive into the summary if they're in the
  source.
- Aim for 8 to 15 words. Compactness matters but not at the cost of losing
  the central fact.
- Use sentence case (capitalize first word, no trailing punctuation).
- Do NOT use periods at the end. Do NOT add bullet markers like "•" or numbers.
- KEY-TERM RULE: if the item contains a parenthesised term — e.g.
  "(databases)", "(stream processing)", "(Apache Kafka)" — that term is the
  canonical short label the author chose for this item. The summary MUST
  incorporate that term as one of its words (not in parentheses, just as part
  of the phrase). Drop the parentheses themselves; keep the term.

Examples (illustrative — do not copy verbatim):

Source: "Store data so that they can find it again later (databases)"
  → "Databases store data for later retrieval"  ✓
  → "Store data later" ✗  (drops the key term "databases")

Source: "On average, a tweet is delivered to about 75 followers, so 4.6k
         tweets per second become 345k writes per second to the home
         timeline caches."
  → "75 followers per tweet → 4.6k tweets/sec become 345k writes/sec"  ✓
  → "Tweets fan out to many followers"  ✗  (drops every concrete number)
  → "Approach 2 prefers work at write time"  ✗  (also drops the numbers)

Source: "In an ideal world, the running time of a batch job is the size of
         the dataset divided by the throughput. In practice, the running
         time is often longer, due to skew and slow tasks."
  → "Batch runtime: ideal = data/throughput, actual is longer from skew"  ✓
  → "Batch job runtime issues"  ✗  (loses the formula and the reason)

Items:
{items}

Output valid JSON only:
{{"summaries": ["summary 1", "summary 2", ...]}}

The summaries array must have the same length as the input items, in the same order."""


def _first_sentence_fallback(text: str) -> str:
    """Take the first sentence of an item as a last-ditch summary.

    Better than the previous "first 5 words" fallback because it gives a
    complete thought rather than an arbitrary clip mid-clause. Strips any
    leading bullet markers so the result doesn't render with a stray "•".
    """
    cleaned = re.sub(r"^[•·\-*]\s*", "", text).strip()
    m = re.match(r"^[^.!?\n]+[.!?]", cleaned)
    if m:
        return m.group(0).rstrip(".!?").strip()
    words = cleaned.split()
    if len(words) > 15:
        return " ".join(words[:15])
    return cleaned


def _parse_summaries(response_text: str, expected: int) -> list[str] | None:
    """Parse and validate the LLM's summary response. Returns a list of
    ``expected`` summaries, or ``None`` if the response is malformed.
    """
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        return None
    summaries = data.get("summaries", [])
    if not isinstance(summaries, list) or len(summaries) != expected:
        return None
    return [str(s).strip().rstrip(".") for s in summaries]


def _summarize_one(item_text: str) -> str | None:
    """Single-item retry: ask the LLM to summarize one item at a time. The
    JSON schema is identical to the batch path but the LLM is much more
    reliable when only producing one summary, so this is the fallback when
    the batch call returns the wrong array length.
    """
    single_prompt = SUMMARY_PROMPT.format(items=f"1. {item_text}")
    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response_text = _call_ollama(single_prompt)
            parsed = _parse_summaries(response_text, expected=1)
            if parsed:
                return parsed[0]
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(
                f"Single-item summary attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}"
            )
    return None


def _generate_list_item_summaries(items: list[str]) -> list[str]:
    """Summarize a list of items into on-screen bullet text. Tries a batch
    call first (one LLM round-trip for the whole list), falls back to
    per-item calls if the batch result is malformed, and finally to the
    first-sentence fallback if even single-item calls fail.

    The cascade exists because the new note-taking SUMMARY_PROMPT is long
    enough that local LLMs occasionally drop items or collapse the JSON
    array into a single string. The per-item retry sidesteps that — each
    call only needs to produce one summary, which is much more reliable.
    """
    if not items:
        return []

    numbered = "\n".join(f"{i + 1}. {text}" for i, text in enumerate(items))
    prompt = SUMMARY_PROMPT.format(items=numbered)

    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response_text = _call_ollama(prompt)
            parsed = _parse_summaries(response_text, expected=len(items))
            if parsed:
                return parsed
            logger.warning(
                f"Batch summary count mismatch on attempt {attempt}/{OLLAMA_MAX_RETRIES}"
            )
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(f"Batch summary attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

    # Fallback 1: per-item retries. One LLM call per item — slower but more
    # reliable than the batch call.
    logger.warning(f"Batch failed; retrying {len(items)} items individually")
    results: list[str] = []
    n_fallback_truncated = 0
    for item in items:
        summary = _summarize_one(item)
        if summary:
            results.append(summary)
        else:
            # Fallback 2: first complete sentence of the source. A real
            # thought instead of an arbitrary 5-word clip.
            results.append(_first_sentence_fallback(item))
            n_fallback_truncated += 1
    if n_fallback_truncated:
        logger.warning(
            f"{n_fallback_truncated}/{len(items)} items fell through to first-sentence fallback"
        )
    return results


def _attach_summaries_to_groups(groups: list[ContentGroup]) -> None:
    """For each group containing LIST_ITEMs, generate summaries via LLM (one call per group)."""
    for group in groups:
        list_items = [el for el in group.elements if el.kind == "LIST_ITEM"]
        # Skip if all items already have summaries (re-run case)
        if not list_items or all(item.summary for item in list_items):
            continue
        item_texts = [item.text for item in list_items]
        summaries = _generate_list_item_summaries(item_texts)
        for item, summary in zip(list_items, summaries):
            item.summary = summary
        logger.info(f"Generated {len(summaries)} list item summaries for group ({group.kind})")


def group_elements(elements: list[Element]) -> list[ContentGroup]:
    """Group elements using LLM associations with structural fallback."""
    has_paragraphs = any(e.kind == "PARAGRAPH" for e in elements)
    has_resources = any(e.kind in RESOURCE_KINDS for e in elements)

    # If nothing to ask the LLM about, use structural grouping directly.
    # (Embedded list detection still requires the LLM — but only when there are paragraphs.)
    if not has_paragraphs:
        logger.info("No paragraphs, using structural grouping")
        groups = _structural_fallback(elements)
        _attach_summaries_to_groups(groups)
        return groups

    # Try LLM-based grouping (associations + embedded-list detection)
    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            prompt = _build_prompt(elements)
            response_text = _call_ollama(prompt)
            associations, list_indices = _parse_llm_response(response_text, elements)
            logger.info(
                f"LLM grouped {len(associations)} paragraphs, "
                f"flagged {len(list_indices)} as embedded lists"
            )
            new_elements, new_associations = _apply_embedded_lists(elements, associations, list_indices)
            groups = _assemble_groups(new_elements, new_associations)
            if has_resources:
                groups = _merge_adjacent_resources(groups)
            _attach_summaries_to_groups(groups)
            return groups
        except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning(f"LLM grouping attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

    logger.warning("LLM grouping failed, falling back to structural grouping")
    groups = _structural_fallback(elements)
    _attach_summaries_to_groups(groups)
    return groups


# --- File I/O ---

CONTENT_GROUPS_DIR = GROUPING_CONTENT_GROUPS_DIR


def group_section_file(section_file: Path) -> list[ContentGroup]:
    """Read a section file and return content groups."""
    content = section_file.read_text(encoding="utf-8", errors="replace")
    elements = parse_section(content)
    return group_elements(elements)


@timer(label="Group section")
def process_section(section_file: Path) -> Path:
    """Group elements for a single section and persist to disk."""
    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    groups_file = CONTENT_GROUPS_DIR / section_file.name

    if groups_file.exists():
        logger.info(f"Already grouped: {groups_file.name}")
        return groups_file

    logger.info(f"Grouping elements for {section_file.name}")
    groups = group_section_file(section_file)
    groups_file.write_text(serialize_groups(groups), encoding="utf-8")
    logger.info(f"Wrote content groups: {groups_file.name} ({len(groups)} groups)")
    return groups_file


# --- Watchdog ---

def start_watcher(executor=None) -> "Observer":
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    from concurrent.futures import ThreadPoolExecutor

    SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()

    class SectionHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            filepath = Path(event.src_path)
            if filepath.suffix == ".txt" and filepath.stem.startswith("section_"):
                if not (CONTENT_GROUPS_DIR / filepath.name).exists():
                    logger.info(f"[watchdog] New section detected: {filepath.name}")
                    _executor.submit(_safe_process, filepath)

    def _safe_process(filepath: Path):
        try:
            process_section(filepath)
        except Exception as e:
            logger.error(f"Grouping failed: {filepath.name} — {e}")

    handler = SectionHandler()
    observer = Observer()
    observer.schedule(handler, str(SECTIONS_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer) -> None:
    observer.stop()
    observer.join()


# --- Batch processing ---

def process_all(executor: "ThreadPoolExecutor") -> None:
    """Process all pending sections concurrently using the shared executor."""
    from concurrent.futures import as_completed

    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)

    pending = [
        f for f in sorted(SECTIONS_DIR.glob("section_*.txt"))
        if not (CONTENT_GROUPS_DIR / f.name).exists()
    ]
    if not pending:
        logger.info("All sections already grouped. Nothing to do.")
        return

    logger.info(f"Found {len(pending)} pending sections. Processing concurrently...")

    futures = {executor.submit(process_section, f): f for f in pending}
    succeeded = failed = 0
    for future in as_completed(futures):
        section_file = futures[future]
        try:
            future.result()
            succeeded += 1
            logger.info(f"[{succeeded + failed}/{len(pending)}] Done: {section_file.name}")
        except Exception as e:
            failed += 1
            logger.error(f"[{succeeded + failed}/{len(pending)}] FAILED: {section_file.name} — {e}")

    logger.info(f"Completed: {succeeded} succeeded, {failed} failed out of {len(pending)}")


# --- CLI ---

def _print_groups(groups: list[ContentGroup]) -> None:
    for i, group in enumerate(groups):
        print(f"\n{'='*60}")
        print(f"Group {i}: kind={group.kind}")
        print(f"{'='*60}")
        for el in group.elements:
            text = el.text if len(el.text) <= 80 else el.text[:80] + "..."
            print(f"  {el.kind}: {text}")
        cap_map = group.caption_for_resource
        if cap_map:
            print(f"  --- captions ---")
            for path, cap in cap_map.items():
                print(f"    {path} → {cap or '(none)'}")


if __name__ == "__main__":
    import sys
    import time
    import argparse
    from concurrent.futures import ThreadPoolExecutor

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="LLM-based element grouper")
    parser.add_argument("section_file", type=str, nargs="?", help="Path to a section file")
    parser.add_argument("--all", action="store_true", help="Process all pending sections (concurrent)")
    parser.add_argument("--watch", action="store_true", help="Watch sections dir for new files")
    args = parser.parse_args()

    _executor = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)

    if args.watch:
        logger.info(f"Watching {SECTIONS_DIR} for new sections...")
        observer = start_watcher(executor=_executor)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_watcher(observer)
            _executor.shutdown(wait=True)
            logger.info("Stopped.")
    elif args.all:
        process_all(executor=_executor)
        _executor.shutdown(wait=True)
    elif args.section_file:
        path = Path(args.section_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        groups = group_section_file(path)
        _print_groups(groups)
    else:
        parser.print_help()
