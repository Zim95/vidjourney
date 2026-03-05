from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
import logging

from .pdf_reader import PdfDocumentData, PdfImageBlock, PdfLine


logger = logging.getLogger(__name__)


@dataclass
class ContentItem:
    kind: str
    text: str | None = None
    page_number: int = 1
    column_index: int = 0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    metadata: dict[str, str | int | float] = field(default_factory=dict)


@dataclass
class CleanedPage:
    page_number: int
    items: list[ContentItem] = field(default_factory=list)


@dataclass
class CleanedDocument:
    pages: list[CleanedPage] = field(default_factory=list)

    def all_items(self) -> list[ContentItem]:
        return [item for page in self.pages for item in page.items]


class TextCleaner:
    _PAGE_NUMBER_PATTERN = re.compile(r"^\s*(?:page\s+)?\d+\s*$", re.IGNORECASE)
    _CAPTION_PATTERN = re.compile(r"^\s*(figure|fig\.?|table)\s+\d+[\.:\-\)]?", re.IGNORECASE)
    _HEADING_PATTERN = re.compile(r"^\s*(?:\d+(?:\.\d+)*)?\s*[A-Z][\w\s\-:,()/]{2,}$")

    def clean(self, document: PdfDocumentData) -> CleanedDocument:
        logger.info("Starting text cleaning", extra={"page_count": len(document.pages)})
        repeated_headers, repeated_footers = self._detect_repeated_headers_footers(document)
        pages: list[CleanedPage] = []

        for page in document.pages:
            logger.debug("Cleaning page", extra={"page_number": page.page_number})
            text_lines = self._ordered_lines(page)
            joined_lines = self._join_broken_lines(text_lines)

            page_items: list[ContentItem] = []
            for line in joined_lines:
                normalized = self._normalize_text(line.text)
                if not normalized:
                    continue
                if normalized in repeated_headers or normalized in repeated_footers:
                    continue

                kind = self._classify_line(normalized, line)
                if kind == "page_number":
                    page_items.append(
                        ContentItem(
                            kind="page_number",
                            text=normalized,
                            page_number=page.page_number,
                            column_index=line.column_index,
                            bbox=line.bbox,
                            metadata={"value": int(re.sub(r"\D", "", normalized) or page.page_number)},
                        )
                    )
                    continue

                page_items.append(
                    ContentItem(
                        kind=kind,
                        text=normalized,
                        page_number=page.page_number,
                        column_index=line.column_index,
                        bbox=line.bbox,
                    )
                )

            for image in page.image_blocks:
                page_items.append(self._image_item(page.page_number, image))

            page_items.sort(key=lambda item: (item.column_index, item.bbox[1], item.bbox[0]))
            pages.append(CleanedPage(page_number=page.page_number, items=page_items))
            logger.debug("Cleaned page", extra={"page_number": page.page_number, "item_count": len(page_items)})

        logger.info("Completed text cleaning", extra={"cleaned_pages": len(pages)})
        return CleanedDocument(pages=pages)

    def _ordered_lines(self, page) -> list[PdfLine]:
        logger.debug("Ordering lines", extra={"page_number": page.page_number})
        lines = [line for block in page.text_blocks for line in block.lines]
        return sorted(lines, key=lambda line: (line.column_index, line.bbox[1], line.bbox[0]))

    def _join_broken_lines(self, lines: list[PdfLine]) -> list[PdfLine]:
        logger.debug("Joining broken lines", extra={"line_count": len(lines)})
        if not lines:
            return []

        merged: list[PdfLine] = []
        pending = lines[0]
        for current in lines[1:]:
            if self._can_merge_lines(pending, current):
                pending = PdfLine(
                    text=self._merge_text(pending.text, current.text),
                    spans=[*pending.spans, *current.spans],
                    bbox=(
                        min(pending.bbox[0], current.bbox[0]),
                        min(pending.bbox[1], current.bbox[1]),
                        max(pending.bbox[2], current.bbox[2]),
                        max(pending.bbox[3], current.bbox[3]),
                    ),
                    page_number=pending.page_number,
                    block_index=pending.block_index,
                    column_index=pending.column_index,
                )
                continue

            merged.append(pending)
            pending = current

        merged.append(pending)
        logger.debug("Joined broken lines", extra={"merged_line_count": len(merged)})
        return merged

    def _can_merge_lines(self, first: PdfLine, second: PdfLine) -> bool:
        if first.column_index != second.column_index:
            return False
        if first.block_index != second.block_index:
            return False

        first_text = first.text.rstrip()
        second_text = second.text.lstrip()
        if not first_text or not second_text:
            return False

        if first_text.endswith("-"):
            return True

        is_sentence_break = first_text.endswith((".", "?", "!", ":", ";"))
        starts_new_list_item = bool(re.match(r"^(?:\d+[\.)]|[-•])\s+", second_text))
        starts_heading_like = second_text.isupper() and len(second_text.split()) <= 8
        return not (is_sentence_break or starts_new_list_item or starts_heading_like)

    @staticmethod
    def _merge_text(first: str, second: str) -> str:
        left = first.rstrip()
        right = second.lstrip()
        if left.endswith("-"):
            return f"{left[:-1]}{right}"
        return f"{left} {right}".strip()

    def _classify_line(self, text: str, line: PdfLine) -> str:
        if self._PAGE_NUMBER_PATTERN.match(text):
            return "page_number"
        if self._CAPTION_PATTERN.match(text):
            return "figure_caption"
        if self._looks_like_code(line, text):
            return "code_block"
        if self._looks_like_table(text):
            return "table"
        if self._looks_like_heading(line, text):
            return "heading"
        return "text"

    @staticmethod
    def _looks_like_code(line: PdfLine, text: str) -> bool:
        monospaced = any("mono" in span.font.lower() or "courier" in span.font.lower() for span in line.spans)
        code_tokens = ["{", "}", "(", ")", "=>", "==", "def ", "class ", "return ", "import ", ";"]
        has_code_token = any(token in text for token in code_tokens)
        starts_with_indent = bool(re.match(r"^\s{4,}\S+", line.text))
        return monospaced or has_code_token or starts_with_indent

    @staticmethod
    def _looks_like_table(text: str) -> bool:
        separators = len(re.findall(r"\s{2,}|\t|\|", text))
        numeric_density = len(re.findall(r"\d", text))
        return separators >= 2 and numeric_density >= 2

    def _looks_like_heading(self, line: PdfLine, text: str) -> bool:
        size_values = [span.size for span in line.spans if span.size > 0]
        avg_size = (sum(size_values) / len(size_values)) if size_values else 0.0
        large_font = avg_size >= 13.0
        heading_pattern = bool(self._HEADING_PATTERN.match(text))
        short_line = len(text.split()) <= 16
        return short_line and (large_font or heading_pattern)

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text)
        normalized = normalized.replace("\u00ad", "")
        normalized = normalized.replace("\u2013", "-").replace("\u2014", "-")
        normalized = normalized.replace("\u2018", "'").replace("\u2019", "'")
        normalized = normalized.replace("\u201c", '"').replace("\u201d", '"')
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _detect_repeated_headers_footers(self, document: PdfDocumentData) -> tuple[set[str], set[str]]:
        logger.debug("Detecting repeated headers/footers", extra={"page_count": len(document.pages)})
        top_counter: Counter[str] = Counter()
        bottom_counter: Counter[str] = Counter()

        for page in document.pages:
            lines = [line for block in page.text_blocks for line in block.lines]
            if not lines:
                continue
            ordered = sorted(lines, key=lambda line: line.bbox[1])
            top_candidates = ordered[:2]
            bottom_candidates = ordered[-2:]
            for line in top_candidates:
                normalized = self._normalize_text(line.text)
                normalized and top_counter.update([normalized])
            for line in bottom_candidates:
                normalized = self._normalize_text(line.text)
                normalized and bottom_counter.update([normalized])

        min_repetition = max(2, len(document.pages) // 3)
        repeated_headers = {text for text, count in top_counter.items() if count >= min_repetition}
        repeated_footers = {text for text, count in bottom_counter.items() if count >= min_repetition}
        logger.debug(
            "Detected repeated headers/footers",
            extra={"header_count": len(repeated_headers), "footer_count": len(repeated_footers)},
        )
        return repeated_headers, repeated_footers

    @staticmethod
    def _image_item(page_number: int, image: PdfImageBlock) -> ContentItem:
        return ContentItem(
            kind="image",
            text=None,
            page_number=page_number,
            column_index=image.column_index,
            bbox=image.bbox,
            metadata={
                "width": image.width or 0,
                "height": image.height or 0,
                "colorspace": image.colorspace or 0,
            },
        )


def clean_document(document: PdfDocumentData) -> CleanedDocument:
    logger.debug("clean_document helper called")
    return TextCleaner().clean(document)
