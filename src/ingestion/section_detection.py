'''
Here we need to detect sections.
The input for this module is:
[
    (page_number, PageElements),
    ...
]
'''

import re
import json
import fitz
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field, replace
from statistics import mean, pstdev

from pygments.lexers import guess_lexer
from pygments.util import ClassNotFound

from src.ingestion.page_elements import (
    AnnotationElement,
    CaptionElement,
    CodeBlockElement,
    DrawingElement,
    HeaderFooterElement,
    HeadingElement,
    ImageElement,
    LinkElement,
    ListItemElement,
    PageElement,
    PageElements,
    PageNumberElement,
    ParagraphElement,
    TableElement,
)


@dataclass
class Sections:
    page_elements: list[tuple[int, PageElements]] = field(default_factory=list)
    ordered_items: list[tuple[int, PageElement]] = field(default_factory=list)
    heading_indices: list[int] = field(default_factory=list)
    sections: list[list[tuple[int, PageElement]]] = field(default_factory=list)

    def _single_line_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _all_elements(self, elements: PageElements) -> list[PageElement]:
        return [
            *elements.headings,
            *elements.paragraphs,
            *elements.list_items,
            *elements.captions,
            *elements.code_blocks,
            *elements.images,
            *elements.tables,
            *elements.drawings,
            *elements.links,
            *elements.annotations,
            *elements.headers_footers,
            *elements.page_numbers,
        ]

    def _format_headings(self) -> None:
        for _page_number, elements in self.page_elements:
            elements.headings = [
                replace(heading, text=self._single_line_text(heading.text))
                for heading in elements.headings
            ]

    def get_ordered_elements(self) -> list[tuple[int, PageElement]]:
        '''
        Get all elements from all pages and order them by their reading order.
        This is useful for section detection because sometimes a section might start on one page and continue on the next page,
        and we want to group them together.
        '''
        ordered_items: list[tuple[int, PageElement]] = []
        for page_number, elements in self.page_elements:
            for element in self._all_elements(elements):
                ordered_items.append((page_number, element))

        ordered_items.sort(key=lambda page_element: (int(page_element[1].reading_order_index), int(page_element[0])))

        self.ordered_items = ordered_items
        return self.ordered_items

    def get_heading_indices(self) -> list[int]:
        '''
        Get the indices of the heading elements in the ordered items list.
        This is useful for section detection because we can use the headings as the boundaries of the sections.
        '''
        self.heading_indices = [
            index for index, (_page_number, element) in enumerate(self.ordered_items)
            if isinstance(element, HeadingElement)
        ]
        return self.heading_indices

    def group_sections(self) -> list[list[tuple[int, PageElement]]]:
        '''
        Group the ordered items into sections based on the heading indices.
        Each section starts with a heading and includes all items until the next heading.
        '''
        if not self.heading_indices:
            self.sections = []
            return []

        grouped_sections: list[list[tuple[int, PageElement]]] = []

        for position, heading_index in enumerate(self.heading_indices):
            next_heading_index = self.heading_indices[position + 1] if position + 1 < len(self.heading_indices) else len(self.ordered_items)
            section_items = self.ordered_items[heading_index:next_heading_index]
            grouped_sections.append(section_items)

        self.sections = grouped_sections
        return self.sections

    def detect_sections(self) -> list[list[tuple[int, PageElement]]]:
        '''
        Detect sections based on headings.
        One heading to the next heading is the section.
        '''
        if not self.page_elements:
            self.ordered_items = []
            self.heading_indices = []
            self.sections = []
            return []

        self._format_headings()

        # first we order all the elements by their reading order regardless of page number.
        # This is because sometimes a section might start on one page and continue on the next page,
        #   and we want to group them together.
        self.get_ordered_elements()

        # get the indices of the heading elements in the ordered items list.
        self.get_heading_indices()

        # group the ordered items into sections based on the heading indices.
        self.group_sections()

        return self.sections


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

        Problem:
        - Blind geometric sorting can over-correct pages that are already in the
          right reading order.
        - But some multi-column pages need correction because extraction order can
          interleave columns.

        What we do:
        - Start from reading_order_index order (source-of-truth baseline).
        - Run geometric reflow only if the page is likely multi-column.
        - When reflowing, sort by row bucket (y), then x, then original index;
          rewrite indices into a contiguous range on that page.
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

        Problem:
        - Sections can span multiple pages.
        - We must not mix geometry from different pages, because each page starts
            its own coordinate system at the top-left.
        - We also want to preserve extracted reading_order_index unless a page
            looks multi-column and likely misordered.

        What we do:
        - Split one section into page groups.
        - For each page, call `_reflow_page_elements`, which keeps index order by
            default and only applies geometric correction when needed.
        - Concatenate pages in ascending page_number to preserve section continuity.
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
    def filter_sections(sections: list[list[tuple[int, PageElement]]]) -> list[list[tuple[int, PageElement]]]:
        '''
        Display sections and interactively select ranges of section numbers to keep.
        Input format:
            [(start_number, end_number), (start_number, end_number), ...]
        '''
        if not sections:
            print("No sections found.")
            return []

        SectionUtils.display_sections(sections)
        # Temporary hardcoded section selection for development.
        parsed_ranges = [(15, 238)]

        # user_input = input(
        #     "Enter section ranges to keep as [(start, end), ...] (press Enter to keep all): "
        # ).strip()
        #
        # if not user_input:
        #     return sections
        #
        # try:
        #     parsed_ranges = literal_eval(user_input)
        # except (ValueError, SyntaxError):
        #     print("Invalid range format. Keeping all sections.")
        #     return sections
        #
        # if not isinstance(parsed_ranges, list):
        #     print("Input must be a list of tuples. Keeping all sections.")
        #     return sections

        selected_indices: set[int] = set()
        total_sections = len(sections)

        for item in parsed_ranges:
            if not isinstance(item, tuple) or len(item) != 2:
                continue

            start_number, end_number = item
            if not isinstance(start_number, int) or not isinstance(end_number, int):
                continue

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
            cleaned_items: list[tuple[int, PageElement]] = []
            for page_number, element in section:
                # skip links for section/video output
                if isinstance(element, LinkElement):
                    continue

                # remove running headers/footers that were misclassified as paragraph text
                if isinstance(element, ParagraphElement) and SectionUtils._is_page_artifact_paragraph(element.text):
                    continue

                # ignore header and footer element
                if isinstance(element, (HeaderFooterElement, PageNumberElement)):
                    continue

                # for vector drawings,
                # if area is <=0.02 means its very small and likely to be a decorative element, so we ignore it.
                # if item count is <=2 means its likely to be a simple line or shape, so we ignore it.
                if isinstance(element, DrawingElement):
                    area = float(element.geometry.norm_bbox.get("width", 0.0)) * float(element.geometry.norm_bbox.get("height", 0.0))
                    if area <= 0.002 or int(element.item_count) <= 2:
                        continue

                # here we fix all hyphenated text issues because they can interfere with the section understanding and also look bad in the final video.
                if isinstance(element, (HeadingElement, ParagraphElement, ListItemElement, CaptionElement)):
                    fixed_text = SectionUtils._fix_hyphenation_text(element.text)
                    if fixed_text != element.text:
                        element = replace(element, text=fixed_text)

                # we tag captions properly because sometimes they are detected as paragraphs, and we want to make sure they are treated as captions in the final video.
                if isinstance(element, ParagraphElement) and SectionUtils._is_caption_text(element.text):
                    element = CaptionElement(
                        page_number=element.page_number,
                        reading_order_index=element.reading_order_index,
                        geometry=element.geometry,
                        text=element.text,
                    )

                cleaned_items.append((page_number, element))
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


class CodeCleanupUtils:
    from src.config.constants import (
        INGESTION_CODE_SQL_KEYWORD_HITS,
        INGESTION_CODE_SYMBOL_DENSITY_MIN,
        INGESTION_CODE_SYMBOL_DENSITY_STRONG,
        INGESTION_CODE_MIN_LINE_COUNT,
        INGESTION_CODE_DEMOTE_SYMBOL_DENSITY,
        INGESTION_CODE_DEMOTE_SQL_HITS,
        INGESTION_CODE_PROSE_MIN_LINE_LENGTH,
        INGESTION_CODE_PROSE_CONNECTOR_KEYWORDS,
    )
    SQL_KEYWORDS = {
        "select", "from", "where", "group", "by", "order", "having", "join", "left", "right",
        "inner", "outer", "on", "insert", "update", "delete", "create", "table", "with", "recursive",
        "union", "begin", "transaction", "commit", "rollback",
    }

    @staticmethod
    def _normalize(text: str) -> str:
        return "\n".join(line.rstrip() for line in (text or "").splitlines()).strip()

    @staticmethod
    def _word_tokens(text: str) -> list[str]:
        return re.findall(r"[A-Za-z_]+", text.lower())

    @staticmethod
    def _code_symbol_density(text: str) -> float:
        symbol_count = len(re.findall(r"[{}\[\]();=<>+\-*/%]", text))
        return symbol_count / max(1, len(text))

    @staticmethod
    def _sql_keyword_hits(text: str) -> int:
        tokens = CodeCleanupUtils._word_tokens(text)
        return sum(1 for token in tokens if token in CodeCleanupUtils.SQL_KEYWORDS)

    @staticmethod
    def _line_count(text: str) -> int:
        return len([line for line in text.splitlines() if line.strip()])

    @staticmethod
    def _ends_with_statement_terminator(text: str) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        return lines[-1].endswith((";", "}", "]", ")"))

    @staticmethod
    def _looks_like_explanatory_prose(text: str) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False

        sentence_like_lines = sum(
            1 for line in lines if len(line.split()) >= CodeCleanupUtils.INGESTION_CODE_PROSE_MIN_LINE_LENGTH and line.endswith((".", ":"))
        )
        connector_pattern = r"\\b(" + "|".join(CodeCleanupUtils.INGESTION_CODE_PROSE_CONNECTOR_KEYWORDS) + r")\\b"
        prose_connectors = len(re.findall(connector_pattern, text.lower()))

        first_line_explainer = lines[0].endswith(":") and ";" not in lines[0]
        return (sentence_like_lines >= max(1, len(lines) // 2) and prose_connectors >= 1) or first_line_explainer

    @staticmethod
    def _looks_like_citation_block(text: str) -> bool:
        normalized = CodeCleanupUtils._normalize(text)
        if not normalized:
            return False

        first_line = normalized.splitlines()[0].strip().lower()
        bracketed_ref = bool(re.match(r"^\[\d+\]", first_line))

        lowered = normalized.lower()
        has_doi = "doi:" in lowered or bool(re.search(r"\b10\.\d{4,9}/\S+", lowered))
        has_isbn = "isbn" in lowered or bool(re.search(r"\b97[89][-\s\d]{8,20}\b", lowered))
        has_publication_markers = sum(
            1
            for marker in (
                "volume", "number", "pages", "acm", "communications",
                "publisher", "press", "verlag", "isbn", "doi",
                "february", "january", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december",
            )
            if marker in lowered
        )
        has_year = bool(re.search(r"\b(19|20)\d{2}\b", lowered))

        if not bracketed_ref:
            return False

        if has_doi or has_isbn:
            return True

        return has_publication_markers >= 2 and has_year

    @staticmethod
    def _looks_like_running_header_artifact(text: str) -> bool:
        normalized = CodeCleanupUtils._normalize(text)
        if not normalized:
            return False

        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return False

        compact = re.sub(r"\s+", " ", normalized)

        if re.search(r"\|\s*\d{1,4}\s*$", compact):
            return True

        if len(lines) == 3 and lines[1] == "|" and re.fullmatch(r"\d{1,4}", lines[2]):
            return True

        if re.match(r"^\d{1,4}\s*\|\s*[A-Za-z]", compact):
            return True

        return False

    @staticmethod
    def _is_confident_code(text: str) -> bool:
        lowered = text.lower()
        if "begin transaction" in lowered and ("commit" in lowered or "rollback" in lowered):
            return True

        sql_hits = CodeCleanupUtils._sql_keyword_hits(text)
        symbol_density = CodeCleanupUtils._code_symbol_density(text)
        line_count = CodeCleanupUtils._line_count(text)

        if sql_hits >= CodeCleanupUtils.INGESTION_CODE_SQL_KEYWORD_HITS and symbol_density >= CodeCleanupUtils.INGESTION_CODE_SYMBOL_DENSITY_MIN:
            return True

        if line_count >= CodeCleanupUtils.INGESTION_CODE_MIN_LINE_COUNT and symbol_density >= CodeCleanupUtils.INGESTION_CODE_SYMBOL_DENSITY_STRONG and CodeCleanupUtils._ends_with_statement_terminator(text):
            return True

        return False

    @staticmethod
    def _should_demote_to_paragraph(text: str) -> bool:
        normalized = CodeCleanupUtils._normalize(text)
        if not normalized:
            return False

        if CodeCleanupUtils._looks_like_running_header_artifact(normalized):
            return True

        if CodeCleanupUtils._looks_like_citation_block(normalized):
            return True

        if CodeCleanupUtils._is_confident_code(normalized):
            return False

        symbol_density = CodeCleanupUtils._code_symbol_density(normalized)
        sql_hits = CodeCleanupUtils._sql_keyword_hits(normalized)
        looks_prose = CodeCleanupUtils._looks_like_explanatory_prose(normalized)

        return looks_prose and symbol_density <= CodeCleanupUtils.INGESTION_CODE_DEMOTE_SYMBOL_DENSITY and sql_hits <= CodeCleanupUtils.INGESTION_CODE_DEMOTE_SQL_HITS

    @staticmethod
    def demote_prose_like_code_blocks(
        sections: list[list[tuple[int, PageElement]]],
    ) -> list[list[tuple[int, PageElement]]]:
        if not sections:
            return []

        cleaned_sections: list[list[tuple[int, PageElement]]] = []
        for section in sections:
            cleaned_items: list[tuple[int, PageElement]] = []
            for page_number, element in section:
                if isinstance(element, CodeBlockElement) and CodeCleanupUtils._should_demote_to_paragraph(element.text):
                    paragraph = ParagraphElement(
                        page_number=element.page_number,
                        reading_order_index=element.reading_order_index,
                        geometry=element.geometry,
                        text=CodeCleanupUtils._normalize(element.text),
                    )
                    cleaned_items.append((page_number, paragraph))
                    continue

                cleaned_items.append((page_number, element))

            cleaned_sections.append(cleaned_items)

        return cleaned_sections


class CodeMergeUtils:

    @staticmethod
    def _is_tiny_separator_paragraph(element: PageElement) -> bool:
        if not isinstance(element, ParagraphElement):
            return False

        lines = [line.strip() for line in element.text.splitlines() if line.strip()]
        if len(lines) > 1:
            return False
        if not lines:
            return True

        text = lines[0]
        return len(text) <= 40

    @staticmethod
    def _symbol_density(text: str) -> float:
        symbol_count = len(re.findall(r"[{}\[\]();=<>+\-*/%:,.]", text))
        return symbol_count / max(1, len(text))

    @staticmethod
    def _indent_ratio(text: str) -> float:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return 0.0
        indented = sum(1 for line in lines if line.startswith((" ", "\t")))
        return indented / len(lines)

    @staticmethod
    def _ends_with_open_structure(text: str) -> bool:
        stripped = text.rstrip()
        if not stripped:
            return False
        return stripped.endswith(("{", "(", "[", ":", "\\", ",", "."))

    @staticmethod
    def _starts_with_continuation(text: str) -> bool:
        stripped = text.lstrip()
        if not stripped:
            return False

        lowered = stripped.lower()
        continuation_prefixes = (
            "else",
            "elif",
            "except",
            "catch",
            "finally",
            ".",
            ")",
            "]",
            "}",
            "&&",
            "||",
            "+",
            "-",
            "*",
            "/",
            "%",
            "=>",
            "->",
            "::",
        )
        return lowered.startswith(continuation_prefixes)

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
            ),
        )

    @staticmethod
    def _is_ignorable_separator_element(element: PageElement) -> bool:
        if isinstance(element, (LinkElement, AnnotationElement)):
            return True
        if isinstance(element, ParagraphElement):
            return CodeMergeUtils._is_page_artifact_paragraph(element.text)
        return False

    @staticmethod
    def _is_page_artifact_paragraph(text: str) -> bool:
        compact = re.sub(r"\s+", " ", (text or "")).strip()
        if not compact:
            return True

        if len(compact) > 90:
            return False

        if re.search(r"\|\s*\d{1,4}\s*$", compact):
            return True

        if re.match(r"^\d{1,4}\s*\|\s*chapter\b", compact, flags=re.IGNORECASE):
            return True

        if re.match(r"^\d{1,4}\s*\|\s*[A-Za-z][\w\s\-:,]+$", compact):
            return True

        if re.match(r"^chapter\s+\d+\b", compact, flags=re.IGNORECASE):
            return True

        return False

    @staticmethod
    def _is_cross_page_continuation(previous: CodeBlockElement, current: CodeBlockElement) -> bool:
        prev_text = previous.text or ""
        curr_text = current.text or ""

        if not prev_text.strip() or not curr_text.strip():
            return False

        if CodeMergeUtils._ends_with_open_structure(prev_text) or CodeMergeUtils._starts_with_continuation(curr_text):
            return True

        prev_indent = CodeMergeUtils._indent_ratio(prev_text)
        curr_indent = CodeMergeUtils._indent_ratio(curr_text)
        prev_symbols = CodeMergeUtils._symbol_density(prev_text)
        curr_symbols = CodeMergeUtils._symbol_density(curr_text)

        return abs(prev_indent - curr_indent) <= 0.25 and abs(prev_symbols - curr_symbols) <= 0.06

    @staticmethod
    def combine_split_code_blocks(
        sections: list[list[tuple[int, PageElement]]],
    ) -> list[list[tuple[int, PageElement]]]:
        if not sections:
            return []

        merged_sections: list[list[tuple[int, PageElement]]] = []

        for section in sections:
            merged_items: list[tuple[int, PageElement]] = []
            index = 0
            total_items = len(section)

            while index < total_items:
                page_number, element = section[index]

                if not isinstance(element, CodeBlockElement):
                    merged_items.append((page_number, element))
                    index += 1
                    continue

                base_page = page_number
                base_element = element
                combined_text_parts = [element.text.rstrip("\n")]
                cursor = index + 1
                merged_any = False

                while cursor < total_items:
                    next_page, next_element = section[cursor]

                    if CodeMergeUtils._is_hard_stop_element(next_element):
                        break

                    if CodeMergeUtils._is_ignorable_separator_element(next_element):
                        cursor += 1
                        continue

                    if isinstance(next_element, CodeBlockElement):
                        same_page = next_page == base_page
                        should_merge = same_page or CodeMergeUtils._is_cross_page_continuation(base_element, next_element)
                        if not should_merge:
                            break

                        combined_text_parts.append(next_element.text.rstrip("\n"))
                        base_element = next_element
                        merged_any = True
                        cursor += 1
                        continue

                    if CodeMergeUtils._is_tiny_separator_paragraph(next_element):
                        if cursor + 1 >= total_items:
                            break

                        lookahead_page, lookahead_element = section[cursor + 1]
                        if not isinstance(lookahead_element, CodeBlockElement):
                            break

                        same_page = lookahead_page == base_page
                        should_merge = same_page or CodeMergeUtils._is_cross_page_continuation(base_element, lookahead_element)
                        if not should_merge:
                            break

                        combined_text_parts.append(lookahead_element.text.rstrip("\n"))
                        base_element = lookahead_element
                        merged_any = True
                        cursor += 2
                        continue

                    break

                if merged_any:
                    merged_block = replace(element, text="\n".join(part for part in combined_text_parts if part))
                    merged_items.append((page_number, merged_block))
                    index = cursor
                else:
                    merged_items.append((page_number, element))
                    index += 1

            merged_sections.append(merged_items)

        return merged_sections


class CodeBlockFormatUtils:
    EXTENSION_BY_ALIAS = {
        "python": "py",
        "py": "py",
        "json": "json",
        "javascript": "js",
        "js": "js",
        "typescript": "ts",
        "ts": "ts",
        "java": "java",
        "kotlin": "kt",
        "scala": "scala",
        "go": "go",
        "golang": "go",
        "rust": "rs",
        "c": "c",
        "cpp": "cpp",
        "c++": "cpp",
        "csharp": "cs",
        "c#": "cs",
        "php": "php",
        "ruby": "rb",
        "rb": "rb",
        "sql": "sql",
        "postgresql": "sql",
        "mysql": "sql",
        "sqlite": "sql",
        "html": "html",
        "xml": "xml",
        "yaml": "yaml",
        "yml": "yaml",
        "toml": "toml",
        "ini": "ini",
        "bash": "sh",
        "shell": "sh",
        "sh": "sh",
    }

    @staticmethod
    def _guess_lexer(text: str):
        try:
            return guess_lexer(text)
        except ClassNotFound:
            return None
        except Exception:
            return None

    @staticmethod
    def _extension_from_lexer(lexer) -> str:
        if lexer is None:
            return "txt"

        aliases = [str(alias).lower() for alias in getattr(lexer, "aliases", []) if alias]
        for alias in aliases:
            if alias in CodeBlockFormatUtils.EXTENSION_BY_ALIAS:
                return CodeBlockFormatUtils.EXTENSION_BY_ALIAS[alias]

        lexer_name = str(getattr(lexer, "name", "")).lower()
        if lexer_name in CodeBlockFormatUtils.EXTENSION_BY_ALIAS:
            return CodeBlockFormatUtils.EXTENSION_BY_ALIAS[lexer_name]

        return "txt"

    @staticmethod
    def _normalize_text(text: str) -> str:
        lines = [line.rstrip() for line in (text or "").splitlines()]
        normalized = "\n".join(lines).strip("\n")
        return normalized

    @staticmethod
    def _format_json(text: str) -> str | None:
        normalized = CodeBlockFormatUtils._normalize_text(text)
        try:
            parsed = json.loads(normalized)
        except Exception:
            return None
        return json.dumps(parsed, indent=2, ensure_ascii=False)

    @staticmethod
    def _format_sql(text: str) -> str:
        normalized = CodeBlockFormatUtils._normalize_text(text)
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return ""

        formatted_lines: list[str] = []
        indent_level = 0

        for raw_line in lines:
            upper = raw_line.upper()

            if upper.startswith(("COMMIT", "ROLLBACK", "END")):
                indent_level = max(0, indent_level - 1)

            formatted_lines.append(f"{'    ' * indent_level}{raw_line}")

            if upper.startswith(("BEGIN", "START TRANSACTION", "CASE")):
                indent_level += 1

        return "\n".join(formatted_lines)

    @staticmethod
    def _format_brace_based(text: str) -> str:
        normalized = CodeBlockFormatUtils._normalize_text(text)
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return ""

        formatted_lines: list[str] = []
        indent_level = 0

        for line in lines:
            stripped = line.strip()

            if stripped.startswith(("}", "]", ")")):
                indent_level = max(0, indent_level - 1)

            formatted_lines.append(f"{'    ' * indent_level}{stripped}")

            if stripped.endswith(("{", "[", "(")):
                indent_level += 1

        return "\n".join(formatted_lines)

    @staticmethod
    def format_for_storage(text: str) -> tuple[str, str]:
        lexer = CodeBlockFormatUtils._guess_lexer(text)
        extension = CodeBlockFormatUtils._extension_from_lexer(lexer)

        if extension == "json":
            formatted_json = CodeBlockFormatUtils._format_json(text)
            if formatted_json is not None:
                return formatted_json, extension
            return CodeBlockFormatUtils._format_brace_based(text), extension

        if extension == "sql":
            return CodeBlockFormatUtils._format_sql(text), extension

        return CodeBlockFormatUtils._format_brace_based(text), extension


class TableDetectionUtils:
    from src.config.constants import (
        INGESTION_TABLE_Y_TOLERANCE,
        INGESTION_TABLE_X_CLUSTER_TOLERANCE,
        INGESTION_TABLE_ROW_SPACING_VARIANCE,
        INGESTION_TABLE_WIDTH_RATIO,
        INGESTION_TABLE_SCORE_THRESHOLD,
    )
    Y_TOLERANCE = INGESTION_TABLE_Y_TOLERANCE
    X_CLUSTER_TOLERANCE = INGESTION_TABLE_X_CLUSTER_TOLERANCE

    @staticmethod
    def _words_in_bbox(page: fitz.Page, bbox: tuple[float, float, float, float]) -> list[tuple]:
        x0, y0, x1, y1 = bbox
        return [
            word
            for word in page.get_text("words")
            if float(word[0]) >= x0 and float(word[1]) >= y0 and float(word[2]) <= x1 and float(word[3]) <= y1
        ]

    @staticmethod
    def _group_lines(words: list[tuple]) -> list[list[tuple]]:
        if not words:
            return []

        sorted_words = sorted(words, key=lambda word: (float(word[1]), float(word[0])))
        lines: list[list[tuple]] = []

        for word in sorted_words:
            y_center = (float(word[1]) + float(word[3])) / 2.0
            matching_line = next(
                (
                    line
                    for line in lines
                    if abs(y_center - mean(((float(item[1]) + float(item[3])) / 2.0) for item in line)) <= TableDetectionUtils.Y_TOLERANCE
                ),
                None,
            )

            if matching_line is None:
                lines.append([word])
            else:
                matching_line.append(word)

        return [sorted(line, key=lambda item: float(item[0])) for line in lines]

    @staticmethod
    def _cluster_count(values: list[float], tolerance: float) -> int:
        if not values:
            return 0
        clusters: list[list[float]] = []
        for value in sorted(values):
            target_cluster = next(
                (cluster for cluster in clusters if abs(value - mean(cluster)) <= tolerance),
                None,
            )
            (target_cluster.append(value) if target_cluster is not None else clusters.append([value]))
        return len(clusters)

    @staticmethod
    def _safe_ratio(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator > 0 else 0.0

    @staticmethod
    def _metrics(page: fitz.Page, bbox: tuple[float, float, float, float]) -> dict[str, float]:
        words = TableDetectionUtils._words_in_bbox(page, bbox)
        lines = TableDetectionUtils._group_lines(words)
        line_count = len(lines)

        page_width = float(page.rect.width)
        bbox_width = max(1.0, float(bbox[2]) - float(bbox[0]))
        bbox_height = max(1.0, float(bbox[3]) - float(bbox[1]))

        line_widths = [
            max(0.0, max(float(word[2]) for word in line) - min(float(word[0]) for word in line))
            for line in lines if line
        ]
        avg_line_width = mean(line_widths) if line_widths else 0.0
        width_ratio = TableDetectionUtils._safe_ratio(avg_line_width, max(1.0, page_width))

        all_word_count = sum(len(line) for line in lines)
        word_count_by_line = [len(line) for line in lines if line]
        word_count_variance = pstdev(word_count_by_line) if len(word_count_by_line) >= 2 else 0.0

        x_starts = [float(line[0][0]) for line in lines if line]
        x_ends = [float(line[-1][2]) for line in lines if line]
        same_left_alignment = TableDetectionUtils._cluster_count(x_starts, TableDetectionUtils.X_CLUSTER_TOLERANCE) <= 1
        right_edge_clusters = TableDetectionUtils._cluster_count(x_ends, TableDetectionUtils.X_CLUSTER_TOLERANCE)

        internal_x_positions = [
            float(word[0])
            for line in lines
            for index, word in enumerate(line)
            if index > 0
        ]
        aligned_columns = TableDetectionUtils._cluster_count(internal_x_positions, TableDetectionUtils.X_CLUSTER_TOLERANCE)

        line_y_centers = [
            mean(((float(word[1]) + float(word[3])) / 2.0) for word in line)
            for line in lines if line
        ]
        row_gaps = [
            line_y_centers[index + 1] - line_y_centers[index]
            for index in range(len(line_y_centers) - 1)
        ]
        row_spacing_variance = pstdev(row_gaps) if len(row_gaps) >= 2 else 0.0

        text_blob = " ".join(str(word[4]) for word in words)
        stopword_hits = len(re.findall(r"\b(the|and|or|to|of|in|is|that|for|with|on|as|by|from)\b", text_blob.lower()))
        stopword_ratio = TableDetectionUtils._safe_ratio(stopword_hits, max(1, len(re.findall(r"\b\w+\b", text_blob))))

        sentence_punctuation_hits = len(re.findall(r"[\.;:!?]", text_blob))
        sentence_punctuation_ratio = TableDetectionUtils._safe_ratio(sentence_punctuation_hits, max(1, len(text_blob)))

        number_hits = len(re.findall(r"\d", text_blob))
        alnum_hits = len(re.findall(r"[A-Za-z0-9]", text_blob))
        numeric_ratio = TableDetectionUtils._safe_ratio(number_hits, max(1, alnum_hits))

        drawing_items = page.get_drawings()
        has_grid_lines = any(
            fitz.Rect(item.get("rect", fitz.Rect(0, 0, 0, 0))).intersects(fitz.Rect(*bbox))
            for item in drawing_items
        )

        return {
            "line_count": float(line_count),
            "aligned_columns": float(aligned_columns),
            "width_ratio": width_ratio,
            "stopword_ratio": stopword_ratio,
            "sentence_punctuation_ratio": sentence_punctuation_ratio,
            "word_count_variance": word_count_variance,
            "same_left_alignment": 1.0 if same_left_alignment else 0.0,
            "right_edge_clusters": float(right_edge_clusters),
            "row_spacing_variance": row_spacing_variance,
            "numeric_ratio": numeric_ratio,
            "has_grid_lines": 1.0 if has_grid_lines else 0.0,
            "word_count": float(all_word_count),
            "bbox_density": TableDetectionUtils._safe_ratio(float(all_word_count), bbox_width * bbox_height),
        }

    @staticmethod
    def evaluate(page: fitz.Page, bbox: tuple[float, float, float, float]) -> tuple[bool, float, list[str]]:
        metrics = TableDetectionUtils._metrics(page, bbox)

        reject_rules: dict[str, callable] = {
            "insufficient_columns": lambda m: m["aligned_columns"] <= 1,
            "insufficient_rows": lambda m: m["line_count"] < 3,
            "flowing_paragraph": lambda m: m["width_ratio"] > TableDetectionUtils.INGESTION_TABLE_WIDTH_RATIO and m["aligned_columns"] <= 1,
        }

        reject_reasons = [name for name, rule in reject_rules.items() if rule(metrics)]
        if reject_reasons:
            return False, -1.0, reject_reasons

        positive_rules: dict[str, tuple[float, callable]] = {
            "aligned_columns": (3.0, lambda m: m["aligned_columns"] >= 2),
            "consistent_rows": (2.0, lambda m: m["line_count"] >= 3 and m["row_spacing_variance"] <= TableDetectionUtils.INGESTION_TABLE_ROW_SPACING_VARIANCE),
            "right_edge_alignment": (1.0, lambda m: m["right_edge_clusters"] >= 2),
            "numeric_heavy": (1.0, lambda m: m["numeric_ratio"] >= 0.08),
            "grid_lines": (4.0, lambda m: m["has_grid_lines"] > 0),
        }

        penalty_rules: dict[str, tuple[float, callable]] = {
            "single_left_alignment": (-3.0, lambda m: m["same_left_alignment"] > 0),
            "wide_lines": (-2.0, lambda m: m["width_ratio"] > TableDetectionUtils.INGESTION_TABLE_WIDTH_RATIO),
            "sentence_punctuation_heavy": (-2.0, lambda m: m["sentence_punctuation_ratio"] > 0.03),
            "high_stopword_ratio": (-2.0, lambda m: m["stopword_ratio"] > 0.35),
            "high_word_count_variance": (-1.0, lambda m: m["word_count_variance"] > 6.0),
        }

        score = sum(weight for _name, (weight, rule) in positive_rules.items() if rule(metrics))
        score += sum(weight for _name, (weight, rule) in penalty_rules.items() if rule(metrics))

        return score >= TableDetectionUtils.INGESTION_TABLE_SCORE_THRESHOLD, score, []


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
        ignorable_types = (LinkElement, AnnotationElement)

        def _is_ignorable(elem: PageElement) -> bool:
            """Return True for elements that should be skipped when joining
            adjacent paragraphs. This includes artifact/tiny-separator paragraphs
            and page number markers placed between paragraphs.
            """
            if isinstance(elem, ParagraphElement):
                text = (elem.text or "").strip()
                return ParagraphUtils._is_artifact(text) or ParagraphMergeUtils._is_tiny_separator_paragraph(elem)
            if isinstance(elem, PageNumberElement):
                # Page numbers are ignorable separators between paragraph blocks
                return True
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
                    merged_items.append((base_page, replace(base_elem, text=combined_text)))
                else:
                    merged_items.append((base_page, base_elem))

            merged_sections.append(merged_items)

        return merged_sections

    @staticmethod
    def process(sections: list[list[tuple[int, PageElement]]]) -> list[list[tuple[int, PageElement]]]:
        """
        Run paragraph merging heuristics on each section. Assumes higher-level
        pre-cleaning has already removed obvious running headers/footers; the
        merge step itself skips artifact-like paragraphs when joining.
        """
        return ParagraphMergeUtils.merge_split_paragraphs(sections)


class SectionWriter:

    @staticmethod
    def _ensure_output_dirs(base_dir: Path) -> dict[str, Path]:
        resources_dir = base_dir / "resources"
        directories = {
            "sections": base_dir,
            "resources": resources_dir,
            "images": resources_dir / "images",
            "code_blocks": resources_dir / "code_blocks",
            "tables": resources_dir / "tables",
            "drawings": resources_dir / "drawings",
        }

        for directory in directories.values():
            directory.mkdir(parents=True, exist_ok=True)

        return directories

    @staticmethod
    def _resource_path_for(
        directories: dict[str, Path],
        section_number: int,
        page_number: int,
        resource_name: str,
        extension: str,
        index: int,
    ) -> Path:
        safe_extension = extension if extension.startswith(".") else f".{extension}"
        filename = f"{section_number}_{page_number}_{resource_name}_{index}{safe_extension}"
        return directories[resource_name] / filename

    @staticmethod
    def _write_resource_and_append(
        *,
        lines: list[str],
        directories: dict[str, Path],
        resource_counters: defaultdict[tuple[int, str], int],
        section_number: int,
        page_number: int,
        resource_name: str,
        extension: str,
        content: str,
        line_prefix: str,
    ) -> None:
        resource_counters[(page_number, resource_name)] += 1
        idx = resource_counters[(page_number, resource_name)]
        resource_path = SectionWriter._resource_path_for(
            directories=directories,
            section_number=section_number,
            page_number=page_number,
            resource_name=resource_name,
            extension=extension,
            index=idx,
        )
        resource_path.write_text(content, encoding="utf-8")
        lines.append(f"{line_prefix} {resource_path.as_posix()}")

    @staticmethod
    def _write_binary_resource_and_append(
        *,
        lines: list[str],
        directories: dict[str, Path],
        resource_counters: defaultdict[tuple[int, str], int],
        section_number: int,
        page_number: int,
        resource_name: str,
        extension: str,
        content: bytes,
        line_prefix: str,
    ) -> None:
        resource_counters[(page_number, resource_name)] += 1
        idx = resource_counters[(page_number, resource_name)]
        resource_path = SectionWriter._resource_path_for(
            directories=directories,
            section_number=section_number,
            page_number=page_number,
            resource_name=resource_name,
            extension=extension,
            index=idx,
        )
        resource_path.write_bytes(content)
        lines.append(f"{line_prefix} {resource_path.as_posix()}")

    @staticmethod
    def _append_paragraphs(lines: list[str], paragraphs: list[str]) -> None:
        for para in paragraphs:
            lines.append(f"PARAGRAPH {para}")

    @staticmethod
    def _write_code_block_and_append(
        *,
        lines: list[str],
        directories: dict[str, Path],
        resource_counters: defaultdict[tuple[int, str], int],
        section_number: int,
        page_number: int,
        code_text: str,
    ) -> None:
        formatted_code, _extension = CodeBlockFormatUtils.format_for_storage(code_text)
        SectionWriter._write_resource_and_append(
            lines=lines,
            directories=directories,
            resource_counters=resource_counters,
            section_number=section_number,
            page_number=page_number,
            resource_name="code_blocks",
            extension="txt",
            content=formatted_code,
            line_prefix="CODE_BLOCK",
        )

    @staticmethod
    def _resolve_image_binary(
        element: ImageElement,
        document: fitz.Document | None,
    ) -> tuple[bytes | None, str]:
        if element.image_bytes:
            return bytes(element.image_bytes), (element.image_ext or "png")

        if document is not None and element.image_xref is not None:
            try:
                extracted = document.extract_image(element.image_xref)
                image_bytes = extracted.get("image")
                image_ext = str(extracted.get("ext", element.image_ext or "png")).lower()
                if isinstance(image_bytes, (bytes, bytearray)):
                    return bytes(image_bytes), image_ext
            except Exception:
                return None, (element.image_ext or "png")

        return None, (element.image_ext or "png")

    @staticmethod
    def _resolve_table_image_binary(
        element: TableElement,
        page_number: int,
        document: fitz.Document | None,
    ) -> tuple[bytes | None, str]:
        if document is None:
            return None, "png"

        try:
            page = document.load_page(page_number - 1)
            bbox = element.geometry.bbox
            base_clip = fitz.Rect(
                float(bbox.get("x0", 0.0)),
                float(bbox.get("y0", 0.0)),
                float(bbox.get("x1", 0.0)),
                float(bbox.get("y1", 0.0)),
            )

            clip = SectionWriter._resolve_table_clip_from_candidates(page=page, base_clip=base_clip)

            if clip.width <= 0 or clip.height <= 0:
                return None, "png"

            pixmap = page.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2), alpha=False)
            return pixmap.tobytes("png"), "png"
        except Exception:
            return None, "png"

    @staticmethod
    def _rect_horizontal_overlap_ratio(first: fitz.Rect, second: fitz.Rect) -> float:
        overlap = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
        base = max(1.0, min(first.width, second.width))
        return overlap / base

    @staticmethod
    def _resolve_table_clip_from_candidates(page: fitz.Page, base_clip: fitz.Rect) -> fitz.Rect:
        candidates: list[fitz.Rect] = [base_clip]

        strategy_kwargs = (
            {},
            {"vertical_strategy": "text", "horizontal_strategy": "lines"},
            {"vertical_strategy": "lines", "horizontal_strategy": "text"},
        )

        for kwargs in strategy_kwargs:
            try:
                found = page.find_tables(**kwargs)
            except Exception:
                continue

            for table in (getattr(found, "tables", None) or []):
                table_bbox = getattr(table, "bbox", None)
                if table_bbox is None:
                    continue

                rect = fitz.Rect(
                    float(table_bbox[0]),
                    float(table_bbox[1]),
                    float(table_bbox[2]),
                    float(table_bbox[3]),
                )
                candidates.append(rect)

        compatible_candidates = [
            rect
            for rect in candidates
            if SectionWriter._rect_horizontal_overlap_ratio(rect, base_clip) >= 0.85
            and abs(rect.y0 - base_clip.y0) <= 40
            and rect.y1 >= base_clip.y1
        ]

        scored_candidates: list[tuple[float, float, fitz.Rect]] = []
        for rect in compatible_candidates:
            is_table_like, score, _reasons = TableDetectionUtils.evaluate(
                page=page,
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
            )
            metrics = TableDetectionUtils._metrics(
                page=page,
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
            )

            prose_heavy = (
                metrics["stopword_ratio"] > 0.32
                and metrics["sentence_punctuation_ratio"] > 0.018
                and metrics["word_count"] > 40
            )
            if prose_heavy:
                continue

            adjusted_score = score + (1.0 if is_table_like else 0.0)
            height = rect.y1 - rect.y0
            scored_candidates.append((adjusted_score, -height, rect))

        best = max(scored_candidates, key=lambda item: (item[0], item[1]), default=(0.0, 0.0, base_clip))[2]

        trimmed_best = SectionWriter._trim_table_clip_to_table_rows(page=page, clip=best, min_y1=base_clip.y1)

        page_rect = page.rect
        return fitz.Rect(
            max(page_rect.x0, trimmed_best.x0),
            max(page_rect.y0, trimmed_best.y0),
            min(page_rect.x1, trimmed_best.x1),
            min(page_rect.y1, trimmed_best.y1),
        )

    @staticmethod
    def _trim_table_clip_to_table_rows(page: fitz.Page, clip: fitz.Rect, min_y1: float) -> fitz.Rect:
        words = TableDetectionUtils._words_in_bbox(page, (clip.x0, clip.y0, clip.x1, clip.y1))
        lines = TableDetectionUtils._group_lines(words)
        if not lines:
            return clip

        def line_text(line: list[tuple]) -> str:
            return " ".join(str(word[4]) for word in line).strip()

        def numeric_ratio(text: str) -> float:
            digits = len(re.findall(r"\d", text))
            alnum = len(re.findall(r"[A-Za-z0-9]", text))
            return digits / max(1, alnum)

        def table_like(line: list[tuple]) -> bool:
            x_starts = [float(word[0]) for word in line]
            internal_cols = TableDetectionUtils._cluster_count(x_starts[1:], TableDetectionUtils.X_CLUSTER_TOLERANCE)
            gaps = sum(
                1
                for index in range(len(line) - 1)
                if float(line[index + 1][0]) - float(line[index][2]) >= 12.0
            )
            text = line_text(line)
            wc = len(text.split())
            ratio = numeric_ratio(text)
            sentence_like = wc >= 10 and text.endswith((".", ";", ":"))

            rule_checks = {
                "cols_and_gaps": lambda: internal_cols >= 1 and gaps >= 1 and wc >= 2,
                "short_cells": lambda: wc <= 6 and gaps >= 1,
                "numeric_table": lambda: ratio >= 0.08 and wc >= 2,
            }
            return any(check() for check in rule_checks.values()) and not sentence_like

        started = False
        non_table_streak = 0
        last_table_y1: float | None = None

        for line in lines:
            if not line:
                continue

            is_table_line = table_like(line)
            line_y1 = max(float(word[3]) for word in line)

            if is_table_line:
                started = True
                non_table_streak = 0
                last_table_y1 = line_y1
                continue

            if started:
                non_table_streak += 1
                if non_table_streak >= 2:
                    break

        if last_table_y1 is None:
            return clip

        padded_y1 = max(min_y1, last_table_y1 + 4.0)
        return fitz.Rect(clip.x0, clip.y0, clip.x1, min(clip.y1, padded_y1))

    @staticmethod
    def _write_table_and_append(
        *,
        element: TableElement,
        page_number: int,
        section_number: int,
        lines: list[str],
        directories: dict[str, Path],
        resource_counters: defaultdict[tuple[int, str], int],
        document: fitz.Document | None,
    ) -> None:
        image_data, image_ext = SectionWriter._resolve_table_image_binary(
            element=element,
            page_number=page_number,
            document=document,
        )

        if image_data is not None:
            SectionWriter._write_binary_resource_and_append(
                lines=lines,
                directories=directories,
                resource_counters=resource_counters,
                section_number=section_number,
                page_number=page_number,
                resource_name="tables",
                extension=image_ext,
                content=image_data,
                line_prefix="TABLE",
            )
            return

        SectionWriter._write_resource_and_append(
            lines=lines,
            directories=directories,
            resource_counters=resource_counters,
            section_number=section_number,
            page_number=page_number,
            resource_name="tables",
            extension="txt",
            content=(
                f"row_count={element.row_count}\n"
                f"column_count={element.column_count}\n"
                f"bbox={element.geometry.bbox}\n"
                f"norm_bbox={element.geometry.norm_bbox}\n"
            ),
            line_prefix="TABLE",
        )

    @staticmethod
    def _write_element_with_handlers(
        *,
        element: PageElement,
        page_number: int,
        section_number: int,
        lines: list[str],
        directories: dict[str, Path],
        resource_counters: defaultdict[tuple[int, str], int],
        document: fitz.Document | None,
    ) -> None:
        line_handlers = {
            HeadingElement: lambda elem: lines.append(f"HEADING {elem.text}"),
            ParagraphElement: lambda elem: SectionWriter._append_paragraphs(lines, ParagraphUtils.clean_artifacts([elem.text])),
            ListItemElement: lambda elem: lines.append(f"LIST_ITEM {elem.text}"),
            CaptionElement: lambda elem: lines.append(f"CAPTION {elem.text}"),
            LinkElement: lambda elem: lines.append(f"LINK uri={elem.uri} destination_page={elem.destination_page}"),
            AnnotationElement: lambda elem: lines.append(f"ANNOTATION kind={elem.kind} content={elem.content}"),
        }

        resource_handlers = {
            CodeBlockElement: lambda elem: SectionWriter._write_code_block_and_append(
                lines=lines,
                directories=directories,
                resource_counters=resource_counters,
                section_number=section_number,
                page_number=page_number,
                code_text=elem.text,
            ),
            ImageElement: lambda elem: (
                lambda image_data, image_ext: SectionWriter._write_binary_resource_and_append(
                    lines=lines,
                    directories=directories,
                    resource_counters=resource_counters,
                    section_number=section_number,
                    page_number=page_number,
                    resource_name="images",
                    extension=image_ext,
                    content=image_data,
                    line_prefix="IMAGE",
                ) if image_data is not None else SectionWriter._write_resource_and_append(
                    lines=lines,
                    directories=directories,
                    resource_counters=resource_counters,
                    section_number=section_number,
                    page_number=page_number,
                    resource_name="images",
                    extension="txt",
                    content=(
                        f"image_index={elem.image_index}\n"
                        f"xref={elem.image_xref}\n"
                        f"bbox={elem.geometry.bbox}\n"
                        f"norm_bbox={elem.geometry.norm_bbox}\n"
                    ),
                    line_prefix="IMAGE",
                )
            )(*SectionWriter._resolve_image_binary(elem, document)),
            TableElement: lambda elem: SectionWriter._write_table_and_append(
                element=elem,
                page_number=page_number,
                section_number=section_number,
                lines=lines,
                directories=directories,
                resource_counters=resource_counters,
                document=document,
            ),
            DrawingElement: lambda elem: SectionWriter._write_resource_and_append(
                lines=lines,
                directories=directories,
                resource_counters=resource_counters,
                section_number=section_number,
                page_number=page_number,
                resource_name="drawings",
                extension="txt",
                content=(
                    f"item_count={elem.item_count}\n"
                    f"bbox={elem.geometry.bbox}\n"
                    f"norm_bbox={elem.geometry.norm_bbox}\n"
                ),
                line_prefix="DRAWING",
            ),
        }

        for element_type, handler in {**line_handlers, **resource_handlers}.items():
            if isinstance(element, element_type):
                handler(element)
                return

    @staticmethod
    def write_sections_to_files(
        sections: list[list[tuple[int, PageElement]]],
        output_dir: Path | str = Path("pipeline") / "sections",
        pdf_path: Path | str | None = None,
    ) -> list[Path]:
        '''
        Persist each section to an individual file and write section resources under:
        pipeline/sections/resources/{images,code_blocks,tables,drawings}
        '''
        if not sections:
            return []

        base_dir = Path(output_dir)
        directories = SectionWriter._ensure_output_dirs(base_dir)
        written_section_files: list[Path] = []

        document: fitz.Document | None = None
        if pdf_path is not None:
            try:
                document = fitz.open(Path(pdf_path))
            except Exception:
                document = None

        try:
            for section_number, section_items in enumerate(sections, start=1):
                section_file = directories["sections"] / f"section_{section_number}.txt"
                resource_counters: defaultdict[tuple[int, str], int] = defaultdict(int)

                lines: list[str] = [f"section_number: {section_number}", ""]
                current_page_number: int | None = None

                for page_number, element in section_items:
                    if page_number != current_page_number:
                        if current_page_number is not None:
                            lines.append("")
                        lines.append(f"page_number: {page_number}")
                        lines.append("")
                        current_page_number = page_number

                    SectionWriter._write_element_with_handlers(
                        element=element,
                        page_number=page_number,
                        section_number=section_number,
                        lines=lines,
                        directories=directories,
                        resource_counters=resource_counters,
                        document=document,
                    )

                section_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
                written_section_files.append(section_file)
        finally:
            if document is not None:
                document.close()

        return written_section_files
