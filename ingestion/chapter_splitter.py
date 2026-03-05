from __future__ import annotations

import re
from dataclasses import dataclass, field
import logging

from .text_cleaner import CleanedDocument, ContentItem


logger = logging.getLogger(__name__)


@dataclass
class Chapter:
    title: str
    start_page: int
    end_page: int
    items: list[ContentItem] = field(default_factory=list)


@dataclass
class ChapteredDocument:
    chapters: list[Chapter] = field(default_factory=list)


class ChapterSplitter:
    _NUMBERED_HEADING_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+(.+)$")

    def split(self, cleaned_document: CleanedDocument) -> ChapteredDocument:
        logger.info("Starting chapter split", extra={"page_count": len(cleaned_document.pages)})
        chapters: list[Chapter] = []
        current = Chapter(title="Introduction", start_page=1, end_page=1, items=[])

        for page in cleaned_document.pages:
            for item in page.items:
                if item.kind == "heading" and item.text:
                    if current.items:
                        current.end_page = item.page_number
                        chapters.append(current)
                        logger.debug("Closed chapter", extra={"title": current.title, "end_page": current.end_page})
                    current = Chapter(
                        title=self._normalize_heading(item.text),
                        start_page=item.page_number,
                        end_page=item.page_number,
                        items=[item],
                    )
                    logger.debug("Opened chapter", extra={"title": current.title, "start_page": current.start_page})
                    continue

                current.items.append(item)
                current.end_page = item.page_number

        if current.items:
            chapters.append(current)
            logger.debug("Closed final chapter", extra={"title": current.title, "end_page": current.end_page})

        logger.info("Completed chapter split", extra={"chapter_count": len(chapters)})
        return ChapteredDocument(chapters=chapters)

    def _normalize_heading(self, heading: str) -> str:
        logger.debug("Normalizing heading", extra={"heading": heading})
        matched = self._NUMBERED_HEADING_PATTERN.match(heading)
        if not matched:
            return heading.strip()
        chapter_number, chapter_title = matched.groups()
        return f"{chapter_number} {chapter_title.strip()}"


def split_into_chapters(cleaned_document: CleanedDocument) -> ChapteredDocument:
    logger.debug("split_into_chapters helper called")
    return ChapterSplitter().split(cleaned_document)
