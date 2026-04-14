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

from src.utils import logger
from src.config.constants import (
    GROUPING_SECTIONS_DIR,
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
)


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

Example input:
[0] HEADING Introduction
[1] PARAGRAPH This chapter introduces key concepts. Consider the following diagram:
[2] IMAGE path/to/diagram.png
[3] CAPTION Figure 1. System overview.
[4] PARAGRAPH The system has three main components.

Example output:
{{"associations": {{"1": [2], "4": []}}}}

Explanation: Paragraph 1 says "Consider the following diagram" which references the IMAGE at index 2. Paragraph 4 does not reference any resource.

Now analyze these elements:
{numbered_elements}"""


# --- Data structures ---

@dataclass
class Element:
    kind: str       # HEADING, PARAGRAPH, LIST_ITEM, IMAGE, TABLE, CAPTION, LINK, etc.
    text: str       # content (path for IMAGE/TABLE/CODE_BLOCK)


@dataclass
class ContentGroup:
    kind: str               # "heading" | "paragraph" | "image" | "code_block" | "table" | "list"
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
          PARAGRAPH We typically think of databases...
          IMAGE pipeline/sections/resources/images/3_27_images_1.png
          CAPTION Figure 1-1. One possible architecture...
    """
    blocks = []
    for i, group in enumerate(groups):
        lines = [f"GROUP {i}: {group.kind}"]
        for el in group.elements:
            lines.append(f"  {el.kind} {el.text}")
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
            # Flush previous group
            if current_kind is not None:
                groups.append(ContentGroup(kind=current_kind, elements=current_elements))
            current_kind = group_match.group(1)
            current_elements = []
            continue

        # Element line: "  KIND text..."
        el_match = re.match(
            r"^(HEADING|PARAGRAPH|LIST_ITEM|IMAGE|TABLE|CAPTION|CODE_BLOCK)\s+(.*)$",
            line,
        )
        if el_match and current_kind is not None:
            current_elements.append(Element(kind=el_match.group(1), text=el_match.group(2).strip()))
            continue

        # Continuation line (no KIND prefix) — append to previous element
        if current_elements and current_elements[-1].kind in ("PARAGRAPH", "LIST_ITEM", "HEADING", "CAPTION"):
            current_elements[-1].text += " " + line

    # Flush last group
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
            r"^(HEADING|PARAGRAPH|LIST_ITEM|IMAGE|TABLE|CAPTION|LINK|ANNOTATION|CODE_BLOCK)\s+(.*)$",
            line,
        )
        if not match:
            if elements and elements[-1].kind in ("PARAGRAPH", "LIST_ITEM", "HEADING", "CAPTION"):
                elements[-1].text += " " + line
            continue

        kind = match.group(1)
        text = match.group(2).strip()
        elements.append(Element(kind=kind, text=text))

    return elements


# --- LLM call ---

def _build_prompt(elements: list[Element]) -> str:
    """Build the LLM prompt with numbered elements."""
    lines = []
    for i, el in enumerate(elements):
        text = el.text
        # For long text, keep start and end (references often appear at the end)
        if len(text) > 300:
            text = text[:150] + " [...] " + text[-150:]
        lines.append(f"[{i}] {el.kind} {text}")
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
            "options": {"num_ctx": 8192, "temperature": 0},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _parse_llm_response(response_text: str, elements: list[Element]) -> dict[int, list[int]]:
    """Parse LLM JSON response into {paragraph_index: [resource_indices]}."""
    data = json.loads(response_text)

    associations = data.get("associations", data)

    result: dict[int, list[int]] = {}
    for key, value in associations.items():
        para_idx = int(key)
        # Validate: must be a PARAGRAPH index
        if para_idx < 0 or para_idx >= len(elements):
            continue
        if elements[para_idx].kind != "PARAGRAPH":
            continue
        # Validate: resource indices must be valid RESOURCE_KINDS
        resource_indices = []
        for r_idx in value:
            r_idx = int(r_idx)
            if 0 <= r_idx < len(elements) and elements[r_idx].kind in RESOURCE_KINDS:
                resource_indices.append(r_idx)
        result[para_idx] = resource_indices

    return result


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

        if el.kind == "PARAGRAPH":
            group_els = [el]
            j = i + 1
            while j < len(elements):
                nxt = elements[j]
                if nxt.kind in ("PARAGRAPH", "HEADING"):
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


def group_elements(elements: list[Element]) -> list[ContentGroup]:
    """Group elements using LLM associations with structural fallback."""
    # Check if there are any paragraphs and resources to associate
    has_paragraphs = any(e.kind == "PARAGRAPH" for e in elements)
    has_resources = any(e.kind in RESOURCE_KINDS for e in elements)

    if not has_paragraphs or not has_resources:
        # No associations needed — use structural grouping directly
        logger.info("No paragraph-resource associations needed, using structural grouping")
        return _structural_fallback(elements)

    # Try LLM-based grouping
    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            prompt = _build_prompt(elements)
            response_text = _call_ollama(prompt)
            associations = _parse_llm_response(response_text, elements)
            logger.info(f"LLM grouped {len(associations)} paragraphs")
            groups = _assemble_groups(elements, associations)
            return _merge_adjacent_resources(groups)
        except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning(f"LLM grouping attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

    logger.warning("LLM grouping failed, falling back to structural grouping")
    return _structural_fallback(elements)


# --- File I/O ---

def group_section_file(section_file: Path) -> list[ContentGroup]:
    """Read a section file and return content groups."""
    content = section_file.read_text(encoding="utf-8", errors="replace")
    elements = parse_section(content)
    return group_elements(elements)


def _print_groups(groups: list[ContentGroup]) -> None:
    """Pretty-print content groups for debugging."""
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
    import argparse

    parser = argparse.ArgumentParser(description="LLM-based element grouper")
    parser.add_argument("section_file", type=str, nargs="?", help="Path to a section file")
    parser.add_argument("--all", action="store_true", help="Process all sections")
    args = parser.parse_args()

    if args.all:
        SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
        for f in sorted(SECTIONS_DIR.glob("section_*.txt")):
            print(f"\n{'#'*60}")
            print(f"# {f.name}")
            print(f"{'#'*60}")
            groups = group_section_file(f)
            _print_groups(groups)
    elif args.section_file:
        path = Path(args.section_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        groups = group_section_file(path)
        _print_groups(groups)
    else:
        parser.print_help()
