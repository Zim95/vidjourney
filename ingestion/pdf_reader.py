from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any

import fitz


logger = logging.getLogger(__name__)


@dataclass
class PdfSpan:
    text: str
    font: str
    size: float
    bbox: tuple[float, float, float, float]


@dataclass
class PdfLine:
    text: str
    spans: list[PdfSpan] = field(default_factory=list)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    page_number: int = 1
    block_index: int = 0
    column_index: int = 0


@dataclass
class PdfTextBlock:
    lines: list[PdfLine] = field(default_factory=list)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    block_index: int = 0
    column_index: int = 0


@dataclass
class PdfImageBlock:
    bbox: tuple[float, float, float, float]
    width: int | None = None
    height: int | None = None
    colorspace: int | None = None
    page_number: int = 1
    block_index: int = 0
    column_index: int = 0


@dataclass
class PdfPageData:
    page_number: int
    width: float
    height: float
    text_blocks: list[PdfTextBlock] = field(default_factory=list)
    image_blocks: list[PdfImageBlock] = field(default_factory=list)


@dataclass
class PdfDocumentData:
    path: Path
    pages: list[PdfPageData] = field(default_factory=list)


class PdfReader:
    def read(self, pdf_path: str | Path) -> PdfDocumentData:
        logger.info("Starting PDF read", extra={"pdf_path": str(pdf_path)})
        path = Path(pdf_path)
        document = fitz.open(path)
        pages: list[PdfPageData] = []
        try:
            for page_index, page in enumerate(document, start=1):
                logger.debug("Reading page", extra={"page_number": page_index})
                page_dict = page.get_text("dict")
                text_blocks, image_blocks = self._extract_blocks(page_dict, page_number=page_index, page_width=page.rect.width)
                pages.append(
                    PdfPageData(
                        page_number=page_index,
                        width=page.rect.width,
                        height=page.rect.height,
                        text_blocks=text_blocks,
                        image_blocks=image_blocks,
                    )
                )
        finally:
            document.close()
            logger.debug("Closed PDF document", extra={"pdf_path": str(path)})
        logger.info("Completed PDF read", extra={"pdf_path": str(path), "page_count": len(pages)})
        return PdfDocumentData(path=path, pages=pages)

    def _extract_blocks(
        self,
        page_dict: dict[str, Any],
        page_number: int,
        page_width: float,
    ) -> tuple[list[PdfTextBlock], list[PdfImageBlock]]:
        logger.debug("Extracting page blocks", extra={"page_number": page_number})
        raw_blocks = page_dict.get("blocks", [])
        text_blocks: list[PdfTextBlock] = []
        image_blocks: list[PdfImageBlock] = []

        for block_index, block in enumerate(raw_blocks):
            bbox = self._bbox(block.get("bbox"))
            column_index = self._column_index(bbox, page_width)

            if block.get("type") == 1:
                image_blocks.append(
                    PdfImageBlock(
                        bbox=bbox,
                        width=block.get("width"),
                        height=block.get("height"),
                        colorspace=block.get("colorspace"),
                        page_number=page_number,
                        block_index=block_index,
                        column_index=column_index,
                    )
                )
                continue

            lines = self._extract_lines(block, page_number=page_number, block_index=block_index, column_index=column_index)
            if not lines:
                continue
            text_blocks.append(
                PdfTextBlock(
                    lines=lines,
                    bbox=bbox,
                    block_index=block_index,
                    column_index=column_index,
                )
            )

        logger.debug(
            "Finished extracting page blocks",
            extra={"page_number": page_number, "text_blocks": len(text_blocks), "image_blocks": len(image_blocks)},
        )
        return text_blocks, image_blocks

    def _extract_lines(self, block: dict[str, Any], page_number: int, block_index: int, column_index: int) -> list[PdfLine]:
        logger.debug(
            "Extracting lines from block",
            extra={"page_number": page_number, "block_index": block_index, "column_index": column_index},
        )
        extracted: list[PdfLine] = []
        for line in block.get("lines", []):
            spans: list[PdfSpan] = []
            pieces: list[str] = []
            for span in line.get("spans", []):
                span_text = str(span.get("text", ""))
                if not span_text:
                    continue
                pieces.append(span_text)
                spans.append(
                    PdfSpan(
                        text=span_text,
                        font=str(span.get("font", "")),
                        size=float(span.get("size", 0.0)),
                        bbox=self._bbox(span.get("bbox")),
                    )
                )

            text = "".join(pieces).strip()
            if not text:
                continue
            extracted.append(
                PdfLine(
                    text=text,
                    spans=spans,
                    bbox=self._bbox(line.get("bbox")),
                    page_number=page_number,
                    block_index=block_index,
                    column_index=column_index,
                )
            )
        logger.debug(
            "Finished extracting lines",
            extra={"page_number": page_number, "block_index": block_index, "line_count": len(extracted)},
        )
        return extracted

    @staticmethod
    def _bbox(raw_bbox: Any) -> tuple[float, float, float, float]:
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) < 4:
            return (0.0, 0.0, 0.0, 0.0)
        try:
            return (float(raw_bbox[0]), float(raw_bbox[1]), float(raw_bbox[2]), float(raw_bbox[3]))
        except (TypeError, ValueError):
            return (0.0, 0.0, 0.0, 0.0)

    @staticmethod
    def _column_index(bbox: tuple[float, float, float, float], page_width: float) -> int:
        middle_x = (bbox[0] + bbox[2]) / 2.0
        return 0 if middle_x <= (page_width / 2.0) else 1


def read_pdf(pdf_path: str | Path) -> PdfDocumentData:
    logger.debug("read_pdf helper called", extra={"pdf_path": str(pdf_path)})
    return PdfReader().read(pdf_path)
