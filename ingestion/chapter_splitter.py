from __future__ import annotations

from dataclasses import dataclass, field
import logging

from .ingestion_constants import (
    CHAPTER_SPLITTER_DEFAULT_TITLE,
    CHAPTER_SPLITTER_ITEM_HANDLER_BY_KEY,
    NUMBERED_HEADING_PATTERN,
)
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
    def split(self, cleaned_document: CleanedDocument) -> ChapteredDocument:
        logger.info("Starting chapter split", extra={"page_count": len(cleaned_document.pages)})
        chapters: list[Chapter] = []
        current = Chapter(title=CHAPTER_SPLITTER_DEFAULT_TITLE, start_page=1, end_page=1, items=[])
        handlers = self._handlers()

        for page in cleaned_document.pages:
            for item in page.items:
                handler_key = self._resolve_handler_key(item)
                current = handlers[handler_key](item, current, chapters)

        {
            True: lambda: self._close_current(chapters, current, is_final=True),
            False: lambda: None,
        }[bool(current.items)]()

        logger.info("Completed chapter split", extra={"chapter_count": len(chapters)})
        return ChapteredDocument(chapters=chapters)

    def _handlers(self):
        return {
            CHAPTER_SPLITTER_ITEM_HANDLER_BY_KEY["heading"]: self._handle_heading,
            CHAPTER_SPLITTER_ITEM_HANDLER_BY_KEY["default"]: self._handle_default,
        }

    @staticmethod
    def _resolve_handler_key(item: ContentItem) -> str:
        is_heading = item.kind == "heading" and bool(item.text)
        return {
            True: CHAPTER_SPLITTER_ITEM_HANDLER_BY_KEY["heading"],
            False: CHAPTER_SPLITTER_ITEM_HANDLER_BY_KEY["default"],
        }[is_heading]

    def _handle_heading(self, item: ContentItem, current: Chapter, chapters: list[Chapter]) -> Chapter:
        {
            True: lambda: self._close_current(chapters, current),
            False: lambda: None,
        }[bool(current.items)]()

        next_chapter = Chapter(
            title=self._normalize_heading(str(item.text)),
            start_page=item.page_number,
            end_page=item.page_number,
            items=[item],
        )
        logger.debug("Opened chapter", extra={"title": next_chapter.title, "start_page": next_chapter.start_page})
        return next_chapter

    @staticmethod
    def _handle_default(item: ContentItem, current: Chapter, _chapters: list[Chapter]) -> Chapter:
        current.items.append(item)
        current.end_page = item.page_number
        return current

    @staticmethod
    def _close_current(chapters: list[Chapter], current: Chapter, is_final: bool = False) -> None:
        chapters.append(current)
        event = {True: "Closed final chapter", False: "Closed chapter"}[is_final]
        logger.debug(event, extra={"title": current.title, "end_page": current.end_page})

    def _normalize_heading(self, heading: str) -> str:
        logger.debug("Normalizing heading", extra={"heading": heading})
        matched = NUMBERED_HEADING_PATTERN.match(heading)
        formatter = {
            True: lambda match: f"{match.group(1)} {match.group(2).strip()}",
            False: lambda _match: heading.strip(),
        }[bool(matched)]
        return formatter(matched)


def split_into_chapters(cleaned_document: CleanedDocument) -> ChapteredDocument:
    logger.debug("split_into_chapters helper called")
    return ChapterSplitter().split(cleaned_document)
