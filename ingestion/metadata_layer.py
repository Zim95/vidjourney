from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from .semantic_layer import SemanticDocument


logger = logging.getLogger(__name__)


@dataclass
class MetadataEnrichedDocument:
    chapters: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"chapters": self.chapters}


class StructuralMetadataLayer:
    def enrich(self, semantic_document: SemanticDocument) -> MetadataEnrichedDocument:
        logger.info("Starting structural metadata enrichment", extra={"chapter_count": len(semantic_document.chapters)})
        enriched_chapters: list[dict[str, Any]] = []

        for chapter in semantic_document.chapters:
            chapter_number = chapter.number or "0"
            chapter_title = chapter.title
            current_section_path: list[str] = [chapter_number]
            heading_stack: list[tuple[int, str, str | None]] = []

            next_heading_cache = self._next_heading_map(chapter.elements)
            enriched_elements: list[dict[str, Any]] = []

            section_index = 0
            previous_heading_text: str | None = None
            for index, element in enumerate(chapter.elements):
                element_dict = element.to_dict()
                if element.type == "heading":
                    section_index = 0
                    current_section_path = self._update_section_path(
                        heading_stack=heading_stack,
                        chapter_number=chapter_number,
                        level=element.level or 1,
                        numbering=element.numbering,
                        heading_text=element.text,
                    )
                    previous_heading_text = element.text
                else:
                    section_index += 1

                element_dict.update(
                    {
                        "chapter_title": chapter_title,
                        "section_path": list(current_section_path),
                        "index_in_section": section_index,
                        "previous_heading": previous_heading_text,
                        "next_heading": next_heading_cache.get(index),
                    }
                )
                enriched_elements.append(element_dict)

            enriched_chapters.append(
                {
                    "title": chapter_title,
                    "number": chapter.number,
                    "elements": enriched_elements,
                }
            )

        logger.info("Completed structural metadata enrichment", extra={"chapter_count": len(enriched_chapters)})
        return MetadataEnrichedDocument(chapters=enriched_chapters)

    @staticmethod
    def _next_heading_map(elements) -> dict[int, str | None]:
        next_map: dict[int, str | None] = {}
        next_heading_text: str | None = None
        for index in range(len(elements) - 1, -1, -1):
            next_map[index] = next_heading_text
            element = elements[index]
            if element.type == "heading":
                next_heading_text = element.text
        return next_map

    @staticmethod
    def _update_section_path(
        heading_stack: list[tuple[int, str, str | None]],
        chapter_number: str,
        level: int,
        numbering: str | None,
        heading_text: str | None,
    ) -> list[str]:
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()

        if numbering:
            path_id = numbering
        else:
            suffix = (heading_text or "section").strip().lower().replace(" ", "_")
            path_id = f"{chapter_number}.{suffix}"

        heading_stack.append((level, path_id, heading_text))
        return [chapter_number, *[item[1] for item in heading_stack]]


def enrich_with_structural_metadata(semantic_document: SemanticDocument) -> MetadataEnrichedDocument:
    logger.debug("enrich_with_structural_metadata helper called")
    return StructuralMetadataLayer().enrich(semantic_document)
