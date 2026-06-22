"""
Deterministic element grouper.

Parses a section file into elements and assembles ContentGroups using
structural rules — no LLM round-trip required. The previous version asked
an LLM to (a) associate paragraphs with figures and (b) flag paragraphs
containing inline numbered lists. Both judgments turned out to be
redundant with simpler mechanisms:

- Paragraph↔resource association: a forward-scan absorbs resources into
  the preceding paragraph group, which matches the DDIA layout (figure
  immediately follows its referencing paragraph) for ~all sections.
  ``_merge_adjacent_resources`` is kept as a safety net for the rare
  HEADING→IMAGE→PARAGRAPH ordering.
- Embedded-list detection: ``_split_embedded_list`` is a self-gating regex
  that returns ``None`` unless the paragraph contains ≥2 list-marker
  matches (``1.``/``2)``/``•``/etc.). Apply it unconditionally to every
  PARAGRAPH and unaffected paragraphs pass through untouched.

Structural rules:
- HEADING → always standalone
- CAPTION → always attaches to preceding IMAGE
- LIST_ITEM → attaches to preceding PARAGRAPH
- LINK / ANNOTATION / DRAWING → skipped (metadata)

Usage:
    python -m src.scene_grouping.llm_grouper pipeline/sections/section_3.txt
    python -m src.scene_grouping.llm_grouper --all
"""
import re
from dataclasses import dataclass
from pathlib import Path

from src.utils import logger, timer
from src.config.constants import (
    GROUPING_SECTIONS_DIR,
    GROUPING_CONTENT_GROUPS_DIR,
)
from src.ingestion.quote_attribution import is_quote, split_quote_and_prose


SECTIONS_DIR = GROUPING_SECTIONS_DIR

SKIP_KINDS = {"LINK", "ANNOTATION", "DRAWING"}
RESOURCE_KINDS = {"IMAGE", "CODE_BLOCK", "TABLE"}

# --- Data structures ---

@dataclass
class Element:
    kind: str       # HEADING, PARAGRAPH, LIST_ITEM, IMAGE, TABLE, CAPTION, LINK, etc.
    text: str       # content (path for IMAGE/TABLE/CODE_BLOCK)


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
            if current_kind is not None:
                groups.append(ContentGroup(kind=current_kind, elements=current_elements))
            current_kind = group_match.group(1)
            current_elements = []
            continue

        # Legacy SUMMARY: lines (older content_groups files have them — the
        # summary layer was removed when scroll/build.py switched to
        # display-equals-narration). Skip them so old files keep parsing.
        if line.startswith("SUMMARY:"):
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

# Roman → int for the tiny subset used as list markers (i…xxx covers far
# more than any real inline list needs).
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10}


def _roman_to_int(s: str) -> int | None:
    s = s.lower()
    if not s or not all(c in _ROMAN_VALUES for c in s):
        return None
    total = 0
    prev = 0
    for c in reversed(s):
        v = _ROMAN_VALUES[c]
        total += v if v >= prev else -v
        prev = v
    return total if total > 0 else None


def _marker_value(match: "re.Match[str]") -> tuple[str, int] | None:
    """Classify a marker match by (type_tag, integer_value). Returns None
    for unrecognised tokens — the caller treats that as "not a list"."""
    stripped = re.sub(r"[()\.\s]", "", match.group(0))
    if not stripped:
        return None
    if stripped in ("•", "·", "*"):
        return "bullet", 0
    if stripped.isdigit():
        return "digit", int(stripped)
    if len(stripped) == 1 and stripped.isalpha() and stripped.islower():
        # Don't classify `i`, `v`, `x` as letters — they belong to the roman
        # bucket so a roman/letter mix doesn't pass the same-type check.
        if stripped in _ROMAN_VALUES:
            return "roman", _ROMAN_VALUES[stripped]
        return "letter", ord(stripped) - ord("a") + 1
    roman = _roman_to_int(stripped)
    if roman is not None and 1 <= roman <= 30:
        return "roman", roman
    return None


def _is_real_inline_list(matches: list["re.Match[str]"]) -> bool:
    """Validate that the matched markers form a real inline list.

    The bare ``_LIST_MARKER_RE`` matches plenty of accidental patterns —
    page references (``... page 6. ... page 10. ... page 18.``), footnote
    markers next to chapter parentheticals (``i. ... Chapter 4)``), and so
    on. These passed through the old LLM-flagged path because the LLM
    judged them not lists; the new unconditional regex doesn't, so we
    validate here. A real list:

    - Has ≥2 markers of the same type (all digit / all letter / all roman /
      all bullet — no mixing).
    - For numbered/lettered/roman types: values strictly increase by 1 and
      start at the type's first value (1 / a / i). This rejects ``6. 10.
      18.`` (skips, doesn't start at 1) and ``i. 4)`` (mixed type).
    - Bullet glyphs (``•`` etc.) are exempt from the sequence check — two
      or more identical bullets are always a list.
    """
    if len(matches) < 2:
        return False
    bullets_only = all(m.group(0).strip() in ("•", "·", "*") for m in matches)
    if bullets_only:
        return True
    classifications = [_marker_value(m) for m in matches]
    if any(c is None for c in classifications):
        return False
    types = {c[0] for c in classifications}
    if len(types) > 1:
        return False
    values = [c[1] for c in classifications]
    if values[0] != 1:
        return False
    return values == list(range(1, len(values) + 1))


def _split_embedded_list(paragraph_text: str) -> tuple[str, list[str]] | None:
    """Split a paragraph into (intro, [items]) if it contains an inline list.

    Returns None unless the matched markers form a real, sequential,
    same-type list (see ``_is_real_inline_list``). The validation step is
    what keeps us safely deterministic — without it, the regex would
    falsely split paragraphs that contain page numbers, footnote refs, or
    chapter parentheticals that happen to match the marker pattern.
    """
    matches = list(_LIST_MARKER_RE.finditer(paragraph_text))
    if not _is_real_inline_list(matches):
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


# --- Assembly ---

def _assemble_groups(elements: list[Element]) -> list[ContentGroup]:
    """Forward-scan assembly of ContentGroups. Resources and list items are
    absorbed into the preceding PARAGRAPH; HEADINGs and QUOTEs are always
    standalone; orphan resources/list_items become their own groups.
    """
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


# --- Element expansion ---

def _expand_embedded_lists(elements: list[Element]) -> list[Element]:
    """Split every PARAGRAPH that contains an inline numbered/bulleted list
    into PARAGRAPH (intro) + LIST_ITEMs.

    ``_split_embedded_list`` is self-gating — it returns ``None`` when a
    paragraph has fewer than 2 list markers — so we can run it on every
    PARAGRAPH; paragraphs that aren't lists pass through unchanged. Replaces
    the old LLM-flag-then-split flow.
    """
    new_elements: list[Element] = []
    for old_idx, el in enumerate(elements):
        if el.kind == "PARAGRAPH":
            split = _split_embedded_list(el.text)
            if split is not None:
                intro, items = split
                new_elements.append(Element(kind="PARAGRAPH", text=intro or el.text))
                for item_text in items:
                    new_elements.append(Element(kind="LIST_ITEM", text=item_text))
                logger.info(f"Split paragraph {old_idx} into intro + {len(items)} list items")
                continue
        new_elements.append(el)
    return new_elements


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
    """Group elements into ContentGroups via deterministic rules:
    1. ``_expand_embedded_lists`` splits inline numbered lists into LIST_ITEMs
    2. ``_assemble_groups`` forward-scans into HEADING / paragraph / quote /
       resource / list groups
    3. ``_merge_adjacent_resources`` safety-nets the rare HEADING→IMAGE→
       PARAGRAPH layout where step 2 would otherwise leave an image standalone
    """
    elements = _expand_embedded_lists(elements)
    groups = _assemble_groups(elements)
    return _merge_adjacent_resources(groups)


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
