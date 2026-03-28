import re
from dataclasses import replace

from src.ingestion.page_elements import (
    AnnotationElement,
    CaptionElement,
    CodeBlockElement,
    DrawingElement,
    HeadingElement,
    ImageElement,
    LinkElement,
    ListItemElement,
    PageElement,
    PageNumberElement,
    ParagraphElement,
    TableElement,
)


class ParagraphUtils:
    @staticmethod
    def _is_artifact(text: str) -> bool:
        compact = re.sub(r"\s+", " ", (text or "")).strip()
        if not compact:
            return True
        # Remove pipes, page numbers, references, short lines, etc.
        patterns = {
            "pipe_number": lambda t: re.search(r"\|\s*\d{1,4}\s*$", t),
            "chapter_pipe": lambda t: re.match(r"^\d{1,4}\s*\|\s*chapter\b", t, flags=re.IGNORECASE),
            "page_number": lambda t: re.match(r"^page\s*\d+$", t, flags=re.IGNORECASE),
            "reference": lambda t: re.match(r"^\[\d+\]", t),
            "short_line": lambda t: len(t) < 8,
        }
        return any(patterns[name](compact) for name in patterns)

    @staticmethod
    def clean_artifacts(paragraphs: list[str]) -> list[str]:
        return [para for para in paragraphs if not ParagraphUtils._is_artifact(para)]

    # Remove dead code: merging and reference combining are handled by ParagraphMergeUtils
    # Only keep artifact cleaning here


class ParagraphMergeUtils:
    @staticmethod
    def _is_tiny_separator_paragraph(element: PageElement) -> bool:
        if not isinstance(element, ParagraphElement):
            return False
        lines = [ln.strip() for ln in (element.text or "").splitlines() if ln.strip()]
        if not lines:
            return True
        if len(lines) > 1:
            return False
        return len(lines[0]) <= 40

    @staticmethod
    def _is_hard_stop_element(element: PageElement) -> bool:
        return isinstance(
            element,
            (
                HeadingElement,
                CaptionElement,
                ImageElement,
                TableElement,
                DrawingElement,
                CodeBlockElement,
            ),
        )

    @staticmethod
    def merge_split_paragraphs(sections: list[list[tuple[int, PageElement]]]) -> list[list[tuple[int, PageElement]]]:
        """
        Merge paragraph elements within a section when they appear split across
        extraction boundaries or pages. Heuristics:
        - Combine consecutive ParagraphElement nodes unless separated by a hard-stop.
        - Ignore tiny separator paragraphs and artifact paragraphs when merging.
        - Preserve the page number of the first paragraph in the merged block.
        - Handle simple hyphenation at line breaks (drop hyphen when joining).
        """
        merged_sections: list[list[tuple[int, PageElement]]] = []

        # Helper lambdas/dicts to keep branching compact
        ignorable_types = (LinkElement, AnnotationElement, PageNumberElement)

        def _is_ignorable(elem: PageElement) -> bool:
            if isinstance(elem, ParagraphElement):
                text = (elem.text or "").strip()
                return ParagraphUtils._is_artifact(text) or ParagraphMergeUtils._is_tiny_separator_paragraph(elem)
            return isinstance(elem, ignorable_types)

        def _join_text(prev: str, nxt: str) -> str:
            prev = prev.rstrip()
            nxt = nxt.strip()
            if not prev:
                return nxt
            if prev.endswith("-"):
                return prev[:-1] + nxt
            return prev + (" " if not prev.endswith((".", "?", "!", '"', ":")) else " ") + nxt

        for section in sections:
            merged_items: list[tuple[int, PageElement]] = []
            idx = 0
            n = len(section)

            while idx < n:
                page_number, element = section[idx]

                if not isinstance(element, ParagraphElement):
                    merged_items.append((page_number, element))
                    idx += 1
                    continue

                base_page = page_number
                base_elem = element
                combined_text = (base_elem.text or "").rstrip()
                idx += 1
                merged_any = False

                while idx < n:
                    next_page, next_elem = section[idx]

                    if ParagraphMergeUtils._is_hard_stop_element(next_elem):
                        break

                    if isinstance(next_elem, ParagraphElement):
                        if _is_ignorable(next_elem):
                            idx += 1
                            continue

                        combined_text = _join_text(combined_text, next_elem.text or "")
                        merged_any = True
                        idx += 1
                        continue

                    if _is_ignorable(next_elem):
                        idx += 1
                        continue

                    break

                if merged_any:
                    merged_elem = replace(base_elem, text=combined_text)
                    merged_items.append((base_page, merged_elem))
                else:
                    merged_items.append((base_page, base_elem))

            merged_sections.append(merged_items)

        return merged_sections

    @staticmethod
    def process(sections: list[list[tuple[int, PageElement]]]) -> list[list[tuple[int, PageElement]]]:
        return ParagraphMergeUtils.merge_split_paragraphs(sections)
