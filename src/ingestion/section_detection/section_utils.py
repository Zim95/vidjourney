import re
import sys
from ast import literal_eval
from collections import defaultdict
from dataclasses import replace

from src.ingestion.page_elements import (
    CaptionElement,
    CodeBlockElement,
    DrawingElement,
    HeaderFooterElement,
    HeadingElement,
    LinkElement,
    ListItemElement,
    PageElement,
    PageNumberElement,
    ParagraphElement,
)


class SectionUtils:

    @staticmethod
    def _is_caption_text(text: str) -> bool:
        return bool(re.match(r"^(figure|fig\.|table)\s*\d*", text.strip(), flags=re.IGNORECASE))

    @staticmethod
    def _fix_hyphenation_text(text: str) -> str:
        fixed = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
        fixed = re.sub(r"(\w)-\s{2,}(\w)", r"\1\2", fixed)
        return fixed

    @staticmethod
    def _is_page_artifact_paragraph(text: str) -> bool:
        compact = re.sub(r"\s+", " ", (text or "")).strip()
        if not compact:
            return True

        if len(compact) > 100:
            return False

        if re.search(r"\|\s*\d{1,4}\s*$", compact):
            return True

        if re.match(r"^\d{1,4}\s*\|\s*[A-Za-z]", compact):
            return True

        if re.match(r"^chapter\s+\d+\b", compact, flags=re.IGNORECASE):
            return True

        return False

    @staticmethod
    def _strip_inline_references(text: str) -> str:
        """Remove inline bracket references like [15], [16, 17], [3, 4, 5] from text."""
        return re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", text)

    @staticmethod
    def _is_bibliography_paragraph(text: str) -> bool:
        """
        Detect full bibliography/reference entries that look like:
        [44] Martin Thompson: "Memory Barriers/Fences," mechanical-sympathy.blogspot.co.uk, July 24, 2011.
        """
        compact = re.sub(r"\s+", " ", (text or "")).strip()
        if not compact:
            return False

        # must start with [N]
        if not re.match(r"^\[\d+\]", compact):
            return False

        lowered = compact.lower()
        has_doi = "doi:" in lowered or bool(re.search(r"\b10\.\d{4,9}/\S+", lowered))
        has_isbn = "isbn" in lowered or bool(re.search(r"\b97[89][-\s\d]{8,20}\b", lowered))

        publication_markers = (
            "volume", "number", "pages", "acm", "communications",
            "publisher", "press", "verlag", "isbn", "doi",
            "february", "january", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
            "proceedings", "conference", "journal", "trans.", "ieee", "arxiv",
        )
        marker_hits = sum(1 for marker in publication_markers if marker in lowered)
        has_year = bool(re.search(r"\b(19|20)\d{2}\b", lowered))

        if has_doi or has_isbn:
            return True

        # URLs are strong signals for bibliography entries (blog posts, online articles)
        has_url = bool(re.search(r"https?://|www\.|\.com|\.org|\.net|\.io|\.edu|\.co\.", lowered))

        if has_year and (marker_hits >= 2 or (marker_hits >= 1 and has_url)):
            return True

        return False

    @staticmethod
    def _is_likely_multi_column_page(page_items: list[tuple[int, PageElement]]) -> bool:
        '''
        Decide whether a page likely needs geometric reflow.

        Problem:
        - We already have global/local reading_order_index from extraction.
        - In most pages, this order is good and should be preserved.
        - In multi-column layouts, extraction order can jump between columns,
          making the narrative hard to follow.

        What we do:
        - Use a lightweight heuristic on text-like elements only.
        - If text starts from both far-left and far-right regions on the same page,
          treat it as likely multi-column and allow geometric reflow.
        - Otherwise, keep original reading order.
        '''
        text_like = [
            element
            for _page_number, element in page_items
            if isinstance(element, (HeadingElement, ParagraphElement, ListItemElement, CaptionElement, CodeBlockElement))
        ]

        if len(text_like) < 6:
            return False

        x_positions = [float(element.geometry.norm_bbox.get("x0", 0.0)) for element in text_like]
        if not x_positions:
            return False

        spread = max(x_positions) - min(x_positions)
        left_count = sum(1 for x in x_positions if x <= 0.45)
        right_count = sum(1 for x in x_positions if x >= 0.55)

        return spread >= 0.25 and left_count >= 2 and right_count >= 2

    @staticmethod
    def _reflow_page_elements(page_items: list[tuple[int, PageElement]]) -> list[tuple[int, PageElement]]:
        '''
        Reflow one page while keeping global reading order as the default.
        '''
        if not page_items:
            return []

        ordered_by_index = sorted(page_items, key=lambda item: int(item[1].reading_order_index))
        if not SectionUtils._is_likely_multi_column_page(ordered_by_index):
            return ordered_by_index

        sorted_items = sorted(
            ordered_by_index,
            key=lambda item: (
                round(float(item[1].geometry.norm_bbox.get("y0", 0.0)) / 0.03),
                float(item[1].geometry.norm_bbox.get("x0", 0.0)),
                int(item[1].reading_order_index),
            ),
        )

        base_index = min(int(item[1].reading_order_index) for item in ordered_by_index)
        return [
            (page_number, replace(element, reading_order_index=base_index + offset))
            for offset, (page_number, element) in enumerate(sorted_items)
        ]

    @staticmethod
    def _reflow_section(section_items: list[tuple[int, PageElement]]) -> list[tuple[int, PageElement]]:
        '''
        Reflow a section page-by-page without breaking cross-page section boundaries.
        '''
        grouped_by_page: dict[int, list[tuple[int, PageElement]]] = defaultdict(list)
        for page_number, element in section_items:
            grouped_by_page[page_number].append((page_number, element))

        reflowed_section: list[tuple[int, PageElement]] = []
        for page_number in sorted(grouped_by_page.keys()):
            reflowed_section.extend(SectionUtils._reflow_page_elements(grouped_by_page[page_number]))

        return reflowed_section

    @staticmethod
    def display_sections(sections: list[list[tuple[int, PageElement]]]) -> None:
        '''
        Display sections as: section_number, page_number, heading
        '''
        for section_number, section_items in enumerate(sections, start=1):
            if not section_items:
                continue

            heading_entry = next(
                ((page_number, element) for page_number, element in section_items if isinstance(element, HeadingElement)),
                None,
            )

            if heading_entry is None:
                continue

            page_number, heading = heading_entry
            heading_text = re.sub(r"\s+", " ", heading.text).strip()
            print(f"{section_number}, {page_number}, {heading_text}")

    @staticmethod
    def _parse_ranges(text: str) -> list[tuple[int, int]] | None:
        """Parse section-range input into a list of ``(start, end)`` tuples.

        Accepts a Python literal (``[(15, 238)]``) or the friendly forms
        ``15-238`` / ``15-238, 250-260`` / ``15`` (single section). Returns
        None if the input is empty or unparseable (caller then keeps all).
        """
        text = text.strip()
        if not text:
            return None

        if text.startswith("[") or text.startswith("("):
            try:
                val = literal_eval(text)
            except (ValueError, SyntaxError):
                return None
            items = val if isinstance(val, (list, tuple)) else []
            if items and all(isinstance(x, int) for x in items) and len(items) == 2:
                items = [tuple(items)]  # a bare "(15, 238)"
            out: list[tuple[int, int]] = []
            for item in items:
                if isinstance(item, (tuple, list)) and len(item) == 2 and all(isinstance(x, int) for x in item):
                    out.append((int(item[0]), int(item[1])))
                elif isinstance(item, int):
                    out.append((item, item))
            return out or None

        out = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                if "-" in part:
                    a, _, b = part.partition("-")
                    out.append((int(a), int(b)))
                else:
                    out.append((int(part), int(part)))
            except ValueError:
                return None
        return out or None

    @staticmethod
    def filter_sections(sections: list[list[tuple[int, PageElement]]]) -> list[list[tuple[int, PageElement]]]:
        '''
        Display the detected sections and interactively pick which to keep —
        used to drop front-matter / index / back-matter per book.

        Section numbers are 1-based and inclusive. Enter keeps everything.
        Accepted input: ``15-238`` · ``15-238, 250-260`` · ``[(15, 238)]``.
        Non-interactive runs (no TTY — e.g. the watchdog cascade) keep all so
        they never block on input.
        '''
        if not sections:
            print("No sections found.")
            return []

        SectionUtils.display_sections(sections)
        total_sections = len(sections)

        if not sys.stdin.isatty():
            print(f"[filter] non-interactive — keeping all {total_sections} sections.")
            return sections

        raw = input(
            "Sections to keep — e.g. 15-238  or  15-238, 250-260  "
            "(Enter = keep all): "
        )
        parsed_ranges = SectionUtils._parse_ranges(raw)
        if parsed_ranges is None:
            print(f"Keeping all {total_sections} sections.")
            return sections

        selected_indices: set[int] = set()
        for start_number, end_number in parsed_ranges:
            start = max(1, min(start_number, end_number))
            end = min(total_sections, max(start_number, end_number))
            for section_number in range(start, end + 1):
                selected_indices.add(section_number - 1)

        filtered_sections = [
            section
            for index, section in enumerate(sections)
            if index in selected_indices
        ]

        if not filtered_sections:
            print("No valid ranges selected. Keeping all sections.")
            return sections

        print(f"Selected {len(filtered_sections)} section(s) out of {total_sections}.")
        return filtered_sections

    @staticmethod
    def _should_skip(element: PageElement) -> bool:
        """Return True if this element should be removed entirely."""
        skip_rules = {
            LinkElement: lambda e: True,
            HeaderFooterElement: lambda e: True,
            PageNumberElement: lambda e: True,
            ParagraphElement: lambda e: (
                SectionUtils._is_page_artifact_paragraph(e.text)
                or SectionUtils._is_bibliography_paragraph(e.text)
            ),
            DrawingElement: lambda e: (
                float(e.geometry.norm_bbox.get("width", 0.0)) * float(e.geometry.norm_bbox.get("height", 0.0)) <= 0.002
                or int(e.item_count) <= 2
            ),
        }
        for element_type, rule in skip_rules.items():
            if isinstance(element, element_type):
                return rule(element)
        return False

    @staticmethod
    def _transform_element(element: PageElement) -> PageElement:
        """Apply text fixes and re-tagging to an element."""
        # fix hyphenation
        text_fixable = (HeadingElement, ParagraphElement, ListItemElement, CaptionElement)
        if isinstance(element, text_fixable):
            fixed_text = SectionUtils._fix_hyphenation_text(element.text)
            if fixed_text != element.text:
                element = replace(element, text=fixed_text)

        # strip inline bracket references
        ref_strippable = (ParagraphElement, ListItemElement)
        if isinstance(element, ref_strippable):
            stripped_text = SectionUtils._strip_inline_references(element.text)
            if stripped_text != element.text:
                element = replace(element, text=stripped_text)

        # re-tag misclassified captions
        if isinstance(element, ParagraphElement) and SectionUtils._is_caption_text(element.text):
            element = CaptionElement(
                page_number=element.page_number,
                reading_order_index=element.reading_order_index,
                geometry=element.geometry,
                text=element.text,
            )

        return element

    @staticmethod
    def preclean_sections(sections: list[list[tuple[int, PageElement]]]) -> list[list[tuple[int, PageElement]]]:
        '''
        Pre-clean selected sections before layout reflow.

        This stage removes obvious noise and normalizes text, but does not
        change layout order yet.

        Noise cleanup strategy:
        - Remove repeating header blocks
        - Remove repeating footer blocks
        - Remove page numbers
        - Remove decorative vectors
        - Fix hyphenation
        - Tag captions (don't delete) ----> Tag image to images, tables, etc.
        '''
        if not sections:
            return []

        precleaned_sections: list[list[tuple[int, PageElement]]] = []

        for section in sections:
            cleaned_items: list[tuple[int, PageElement]] = [
                (page_number, SectionUtils._transform_element(element))
                for page_number, element in section
                if not SectionUtils._should_skip(element)
            ]
            precleaned_sections.append(cleaned_items)

        return precleaned_sections

    @staticmethod
    def reflow_sections(precleaned_sections: list[list[tuple[int, PageElement]]]) -> list[list[tuple[int, PageElement]]]:
        '''
        Apply section reflow after pre-cleaning.

        Input:
        - precleaned sections where obvious noise is already removed.

        Output:
        - noise_removed_sections with page-wise reflow applied through
          `reflow_section`.
        '''
        if not precleaned_sections:
            return []

        noise_removed_sections: list[list[tuple[int, PageElement]]] = []
        for section in precleaned_sections:
            noise_removed_sections.append(SectionUtils._reflow_section(section))

        return noise_removed_sections
