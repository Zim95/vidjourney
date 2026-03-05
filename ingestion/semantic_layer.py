from __future__ import annotations

import re
from dataclasses import dataclass, field
import logging
from typing import Any

from .chapter_splitter import ChapteredDocument
from .text_cleaner import ContentItem


logger = logging.getLogger(__name__)


@dataclass
class SemanticElement:
    type: str
    page: int
    text: str | None = None
    level: int | None = None
    numbering: str | None = None
    ordered: bool | None = None
    items: list[str] | None = None
    bbox: tuple[float, float, float, float] | None = None
    caption: str | None = None
    image_reference: str | None = None
    emphasis: list[dict[str, str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "type": self.type,
            "page": self.page,
            "text": self.text,
            "level": self.level,
            "numbering": self.numbering,
            "ordered": self.ordered,
            "items": self.items,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "caption": self.caption,
            "image_reference": self.image_reference,
            "emphasis": self.emphasis,
        }
        return {key: value for key, value in data.items() if value is not None}


@dataclass
class SemanticChapter:
    title: str
    number: str | None
    elements: list[SemanticElement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "number": self.number,
            "elements": [element.to_dict() for element in self.elements],
        }


@dataclass
class SemanticDocument:
    chapters: list[SemanticChapter] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"chapters": [chapter.to_dict() for chapter in self.chapters]}


class SemanticLayer:
    _NUMBERED_HEADING_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+(.+?)\s*$")
    _CHAPTER_PATTERN = re.compile(r"^\s*chapter\s+(\d+)\s*[:\-]?\s*(.*?)\s*$", re.IGNORECASE)
    _BULLET_PATTERN = re.compile(r"^\s*([•\-*])\s+(.*)\s*$")
    _ORDERED_LIST_PATTERN = re.compile(r"^\s*((?:\d+\.|\([ivxlcdm]+\)|[a-z]\)))\s+(.*)\s*$", re.IGNORECASE)

    def build(self, chaptered_document: ChapteredDocument) -> SemanticDocument:
        logger.info("Starting semantic layer build", extra={"chapter_count": len(chaptered_document.chapters)})
        chapters: list[SemanticChapter] = []
        for chapter in chaptered_document.chapters:
            chapter_number, chapter_title = self._extract_chapter_identity(chapter.title)
            semantic_chapter = SemanticChapter(title=chapter_title, number=chapter_number, elements=[])
            semantic_chapter.elements = self._build_semantic_elements(chapter.items)
            chapters.append(semantic_chapter)
            logger.debug(
                "Built semantic chapter",
                extra={"title": chapter_title, "number": chapter_number or "", "element_count": len(semantic_chapter.elements)},
            )
        logger.info("Completed semantic layer build", extra={"chapter_count": len(chapters)})
        return SemanticDocument(chapters=chapters)

    def _build_semantic_elements(self, items: list[ContentItem]) -> list[SemanticElement]:
        logger.debug("Building semantic elements", extra={"item_count": len(items)})
        raw_elements: list[SemanticElement] = []
        for item in items:
            mapped = self._map_item(item)
            mapped is not None and raw_elements.append(mapped)

        merged = self._merge_contiguous_elements(raw_elements)
        listed = self._group_list_elements(merged)
        attached = self._attach_captions(listed)
        logger.debug("Built semantic elements", extra={"element_count": len(attached)})
        return attached

    def _map_item(self, item: ContentItem) -> SemanticElement | None:
        logger.debug("Mapping content item", extra={"kind": item.kind, "page": item.page_number})
        if item.kind in {"page_number"}:
            return None

        if item.kind == "heading":
            numbering, heading_text = self._extract_heading_number(item.text or "")
            level = self._heading_level(numbering=numbering, text=heading_text)
            return SemanticElement(
                type="heading",
                page=item.page_number,
                text=heading_text,
                level=level,
                numbering=numbering,
            )

        if item.kind == "code_block":
            return SemanticElement(type="code_block", page=item.page_number, text=item.text or "")

        if item.kind == "image":
            return SemanticElement(
                type="image",
                page=item.page_number,
                bbox=item.bbox,
                image_reference=f"page_{item.page_number}_image_{int(item.bbox[0])}_{int(item.bbox[1])}",
            )

        if item.kind == "figure_caption":
            return SemanticElement(type="caption", page=item.page_number, text=item.text or "")

        if item.kind == "table":
            return SemanticElement(
                type="table",
                page=item.page_number,
                image_reference=f"page_{item.page_number}_table_{int(item.bbox[0])}_{int(item.bbox[1])}",
                bbox=item.bbox,
                text=item.text or "",
            )

        return SemanticElement(type="paragraph", page=item.page_number, text=item.text or "")

    def _merge_contiguous_elements(self, elements: list[SemanticElement]) -> list[SemanticElement]:
        logger.debug("Merging contiguous semantic elements", extra={"input_count": len(elements)})
        if not elements:
            return []

        merged: list[SemanticElement] = []
        pending = elements[0]
        for current in elements[1:]:
            if self._can_merge(pending, current):
                pending = self._merge_pair(pending, current)
                continue
            merged.append(pending)
            pending = current

        merged.append(pending)
        logger.debug("Merged contiguous semantic elements", extra={"output_count": len(merged)})
        return merged

    @staticmethod
    def _can_merge(left: SemanticElement, right: SemanticElement) -> bool:
        same_page = left.page == right.page
        if not same_page:
            return False
        if left.type == "paragraph" and right.type == "paragraph":
            return True
        if left.type == "code_block" and right.type == "code_block":
            return True
        return False

    @staticmethod
    def _merge_pair(left: SemanticElement, right: SemanticElement) -> SemanticElement:
        delimiter = "\n" if left.type == "code_block" else " "
        left_text = left.text or ""
        right_text = right.text or ""
        merged_text = f"{left_text}{delimiter}{right_text}".strip()
        return SemanticElement(type=left.type, page=left.page, text=merged_text)

    def _group_list_elements(self, elements: list[SemanticElement]) -> list[SemanticElement]:
        logger.debug("Grouping list elements", extra={"input_count": len(elements)})
        grouped: list[SemanticElement] = []
        list_items: list[str] = []
        list_ordered: bool | None = None
        list_page: int | None = None

        for element in elements:
            marker = self._list_marker(element)
            if marker is None:
                if list_items:
                    grouped.append(SemanticElement(type="list", page=list_page or element.page, ordered=bool(list_ordered), items=list_items))
                    list_items = []
                    list_ordered = None
                    list_page = None
                grouped.append(element)
                continue

            ordered, text = marker
            if not list_items:
                list_ordered = ordered
                list_page = element.page
                list_items.append(text)
                continue

            if element.page != list_page or ordered != list_ordered:
                grouped.append(SemanticElement(type="list", page=list_page or element.page, ordered=bool(list_ordered), items=list_items))
                list_items = [text]
                list_ordered = ordered
                list_page = element.page
                continue

            list_items.append(text)

        if list_items:
            grouped.append(SemanticElement(type="list", page=list_page or 1, ordered=bool(list_ordered), items=list_items))

        logger.debug("Grouped list elements", extra={"output_count": len(grouped)})
        return grouped

    def _list_marker(self, element: SemanticElement) -> tuple[bool, str] | None:
        if element.type != "paragraph" or not element.text:
            return None

        bullet_match = self._BULLET_PATTERN.match(element.text)
        if bullet_match:
            return (False, bullet_match.group(2).strip())

        ordered_match = self._ORDERED_LIST_PATTERN.match(element.text)
        if ordered_match:
            return (True, ordered_match.group(2).strip())

        return None

    @staticmethod
    def _attach_captions(elements: list[SemanticElement]) -> list[SemanticElement]:
        logger.debug("Attaching captions", extra={"input_count": len(elements)})
        attached: list[SemanticElement] = []
        for element in elements:
            if element.type != "caption":
                attached.append(element)
                continue

            attach_index = next(
                (index for index in range(len(attached) - 1, -1, -1) if attached[index].type in {"image", "table"}),
                None,
            )
            if attach_index is None:
                attached.append(element)
                continue

            attach_target = attached[attach_index]
            attach_target.caption = element.text

        logger.debug("Caption attachment complete", extra={"output_count": len(attached)})
        return attached

    @classmethod
    def _extract_heading_number(cls, text: str) -> tuple[str | None, str]:
        chapter_match = cls._CHAPTER_PATTERN.match(text)
        if chapter_match:
            chapter_number, chapter_title = chapter_match.groups()
            normalized_title = chapter_title.strip() or f"Chapter {chapter_number}"
            return chapter_number.strip(), normalized_title

        numbered_match = cls._NUMBERED_HEADING_PATTERN.match(text)
        if numbered_match:
            number, title = numbered_match.groups()
            return number.strip(), title.strip()

        return None, text.strip()

    @staticmethod
    def _heading_level(numbering: str | None, text: str) -> int:
        if numbering:
            return max(1, min(6, numbering.count(".") + 1))
        if text.isupper() and len(text.split()) <= 10:
            return 1
        return 2

    @classmethod
    def _extract_chapter_identity(cls, title: str) -> tuple[str | None, str]:
        chapter_match = cls._CHAPTER_PATTERN.match(title)
        if chapter_match:
            chapter_number, chapter_title = chapter_match.groups()
            clean_title = chapter_title.strip() or f"Chapter {chapter_number}"
            return chapter_number.strip(), clean_title

        numbered_match = cls._NUMBERED_HEADING_PATTERN.match(title)
        if numbered_match:
            number, chapter_title = numbered_match.groups()
            return number.strip(), chapter_title.strip()

        return None, title.strip()


def build_semantic_document(chaptered_document: ChapteredDocument) -> SemanticDocument:
    logger.debug("build_semantic_document helper called")
    return SemanticLayer().build(chaptered_document)
