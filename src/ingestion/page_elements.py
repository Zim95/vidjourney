'''
Here we are trying to detect every element on the page.

PageElement:
------------
A page consists of:
- Text blocks (headings, paragraphs, list items, captions, code blocks)
- Images
- Tables
- Drawings
- Links
- Annotations
- Headers and footers
- Page numbers

Reading Order of a Page Element:
--------------------------------
Our job is to detect elements in their reading_order.

Reading order means what appears after what in a page.
In every page the reading order starts from top-left to bottom-right.
We represent this starting from 0.

The reading order of each element in a page is local.
We need to make them global when we combine the pages together.

That way we have a complete reading order of the entire pdf.
'''

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from statistics import median
from typing import Any, Callable, ClassVar

import fitz


BBox = tuple[float, float, float, float]


class GeometryBase:
    @staticmethod
    def normalize_bbox(bbox: BBox, page_width: float, page_height: float) -> dict[str, float]:
        '''
        Normalization: What are we doing?

        - Basically PDF pages have different size.
        - When reading with fitz we get bbox (Bounding Box) of each text block, line, word, etc. in PDF coordinate system.
        - The bbox will have coordinates (x0, y0, x1, y1) where (x0, y0) is the top-left corner and (x1, y1) is the bottom-right corner of the block.
        - The same bbox might have different coordinate values for different page sizes.
            - Example,
                bbox = (100, 150, 300, 200) on a page of size (600, 800)
                Might be,
                bbox = (95, 140, 290, 190) on a page of size (1200, 1600)
        - To make the coordinates consistent across different page sizes, we can normalize them to a common coordinate system, such as a unit square (0, 0) to (1, 1).
        - Imagine using relative coordinates instead of absolute coordinates.
            - So instead of saying "the block is at (100, 150) on a 600x800 page", we can say "the block is at (0.167, 0.188) in normalized coordinates".
            - Or 12% from left, 18.8% from top, 33.3% width, 6.25% height.
        - This way, the same block will have the same normalized coordinates regardless of the page size.
        '''
        x0, y0, x1, y1 = bbox
        safe_width = page_width if page_width > 0 else 1.0
        safe_height = page_height if page_height > 0 else 1.0

        nx0 = x0 / safe_width
        ny0 = y0 / safe_height
        nx1 = x1 / safe_width
        ny1 = y1 / safe_height

        return {
            "x0": nx0,
            "y0": ny0,
            "x1": nx1,
            "y1": ny1,
            "width": max(0.0, nx1 - nx0),
            "height": max(0.0, ny1 - ny0),
        }


@dataclass(frozen=True)
class Geometry(GeometryBase):
    bbox: dict[str, float]
    norm_bbox: dict[str, float]

    @staticmethod
    def _bbox_dict(bbox: BBox) -> dict[str, float]:
        return {
            "x0": bbox[0],
            "y0": bbox[1],
            "x1": bbox[2],
            "y1": bbox[3],
            "width": max(0.0, bbox[2] - bbox[0]),
            "height": max(0.0, bbox[3] - bbox[1]),
        }

    @classmethod
    def from_bbox(cls, bbox: BBox, page_width: float, page_height: float) -> Geometry:
        return cls(
            bbox=cls._bbox_dict(bbox),
            norm_bbox=cls.normalize_bbox(bbox=bbox, page_width=page_width, page_height=page_height),
        )


@dataclass(frozen=True)
class PageElement:
    page_number: int
    reading_order_index: int
    geometry: Geometry

    def apply_reading_order_base(self, reading_order_base: int) -> PageElement:
        return replace(self, reading_order_index=self.reading_order_index + reading_order_base)


@dataclass(frozen=True)
class HeadingElement(PageElement):
    text: str
    font_size: float


@dataclass(frozen=True)
class ParagraphElement(PageElement):
    text: str


@dataclass(frozen=True)
class ListItemElement(PageElement):
    text: str


@dataclass(frozen=True)
class CaptionElement(PageElement):
    text: str


@dataclass(frozen=True)
class CodeBlockElement(PageElement):
    text: str


@dataclass(frozen=True)
class ImageElement(PageElement):
    image_index: int


@dataclass(frozen=True)
class TableElement(PageElement):
    row_count: int
    column_count: int


@dataclass(frozen=True)
class DrawingElement(PageElement):
    item_count: int


@dataclass(frozen=True)
class LinkElement(PageElement):
    uri: str | None
    destination_page: int | None


@dataclass(frozen=True)
class AnnotationElement(PageElement):
    kind: str
    content: str | None


@dataclass(frozen=True)
class HeaderFooterElement(PageElement):
    text: str
    region: str


@dataclass(frozen=True)
class PageNumberElement(PageElement):
    text: str


@dataclass
class PageElements:
    image_index: ClassVar[int] = 0
    render_order_index: ClassVar[int] = 0

    page_number: int
    page_width: float
    page_height: float
    headings: list[HeadingElement] = field(default_factory=list)
    paragraphs: list[ParagraphElement] = field(default_factory=list)
    list_items: list[ListItemElement] = field(default_factory=list)
    captions: list[CaptionElement] = field(default_factory=list)
    code_blocks: list[CodeBlockElement] = field(default_factory=list)
    images: list[ImageElement] = field(default_factory=list)
    tables: list[TableElement] = field(default_factory=list)
    drawings: list[DrawingElement] = field(default_factory=list)
    links: list[LinkElement] = field(default_factory=list)
    annotations: list[AnnotationElement] = field(default_factory=list)
    headers_footers: list[HeaderFooterElement] = field(default_factory=list)
    page_numbers: list[PageNumberElement] = field(default_factory=list)

    def apply_reading_order_base(self, reading_order_base: int) -> PageElements:
        if reading_order_base == 0:
            return self

        self.headings = [element.apply_reading_order_base(reading_order_base) for element in self.headings]
        self.paragraphs = [element.apply_reading_order_base(reading_order_base) for element in self.paragraphs]
        self.list_items = [element.apply_reading_order_base(reading_order_base) for element in self.list_items]
        self.captions = [element.apply_reading_order_base(reading_order_base) for element in self.captions]
        self.code_blocks = [element.apply_reading_order_base(reading_order_base) for element in self.code_blocks]
        self.images = [element.apply_reading_order_base(reading_order_base) for element in self.images]
        self.tables = [element.apply_reading_order_base(reading_order_base) for element in self.tables]
        self.drawings = [element.apply_reading_order_base(reading_order_base) for element in self.drawings]
        self.links = [element.apply_reading_order_base(reading_order_base) for element in self.links]
        self.annotations = [element.apply_reading_order_base(reading_order_base) for element in self.annotations]
        self.headers_footers = [element.apply_reading_order_base(reading_order_base) for element in self.headers_footers]
        self.page_numbers = [element.apply_reading_order_base(reading_order_base) for element in self.page_numbers]
        return self

    @staticmethod
    def _to_bbox_tuple(bbox_like: Any) -> BBox:
        if isinstance(bbox_like, fitz.Rect):
            return (float(bbox_like.x0), float(bbox_like.y0), float(bbox_like.x1), float(bbox_like.y1))
        if isinstance(bbox_like, (list, tuple)) and len(bbox_like) >= 4:
            return (float(bbox_like[0]), float(bbox_like[1]), float(bbox_like[2]), float(bbox_like[3]))
        return (0.0, 0.0, 0.0, 0.0)

    @staticmethod
    def _line_text(line: dict[str, Any]) -> str:
        spans = line.get("spans", [])
        return "".join(str(span.get("text", "")) for span in spans).strip()

    @staticmethod
    def _block_text(block: dict[str, Any]) -> str:
        lines = block.get("lines", [])
        joined_lines = [PageElements._line_text(line) for line in lines]
        return "\n".join(text_line for text_line in joined_lines if text_line)

    @staticmethod
    def _block_font_sizes(block: dict[str, Any]) -> list[float]:
        lines = block.get("lines", [])
        sizes: list[float] = []
        for line in lines:
            for span in line.get("spans", []):
                try:
                    sizes.append(float(span.get("size", 0.0)))
                except (TypeError, ValueError):
                    continue
        return sizes

    @staticmethod
    def _block_fonts(block: dict[str, Any]) -> list[str]:
        lines = block.get("lines", [])
        fonts: list[str] = []
        for line in lines:
            for span in line.get("spans", []):
                font_name = str(span.get("font", "")).strip()
                if font_name:
                    fonts.append(font_name)
        return fonts

    @staticmethod
    def _is_list_item(text: str) -> bool:
        stripped = text.strip()
        return bool(stripped.startswith(("- ", "• ", "* ")) or stripped[:2].isdigit() and stripped[2:3] in {".", ")"})

    @staticmethod
    def _is_caption(text: str) -> bool:
        stripped = text.strip().lower()
        return stripped.startswith(("figure ", "fig. ", "table "))

    @staticmethod
    def _is_page_number(text: str) -> bool:
        stripped = text.strip()
        return stripped.isdigit() and len(stripped) <= 4

    @staticmethod
    def _is_monospace(font_names: list[str]) -> bool:
        lowered = [font_name.lower() for font_name in font_names]
        tokens = ("courier", "mono", "consolas", "menlo")
        return any(any(token in font_name for token in tokens) for font_name in lowered)

    @classmethod
    def reset_indices(cls) -> None:
        cls.image_index = 0
        cls.render_order_index = 0

    @classmethod
    def _next_render_order_index(cls) -> int:
        current_index = cls.render_order_index
        cls.render_order_index += 1
        return current_index

    @classmethod
    def _next_image_index(cls) -> int:
        cls.image_index += 1
        return cls.image_index

    @classmethod
    def _append_image_block(
        cls,
        detected: PageElements,
        page_number: int,
        page_width: float,
        page_height: float,
        bbox: BBox,
    ) -> None:
        detected.images.append(
            ImageElement(
                page_number=page_number,
                reading_order_index=cls._next_render_order_index(),
                geometry=Geometry.from_bbox(bbox=bbox, page_width=page_width, page_height=page_height),
                image_index=cls._next_image_index(),
            )
        )

    @classmethod
    def _append_non_image_block(
        cls,
        block: dict[str, Any],
        detected: PageElements,
        page_number: int,
        page_width: float,
        page_height: float,
        body_font_size: float,
    ) -> None:
        bbox = cls._to_bbox_tuple(block.get("bbox", (0.0, 0.0, 0.0, 0.0)))
        geometry = Geometry.from_bbox(bbox=bbox, page_width=page_width, page_height=page_height)
        current_render_index = cls.render_order_index

        text = cls._block_text(block)
        if not text.strip():
            cls.render_order_index += 1
            return

        font_sizes = cls._block_font_sizes(block)
        max_font_size = max(font_sizes) if font_sizes else body_font_size
        font_names = cls._block_fonts(block)
        norm_y0 = float(geometry.norm_bbox["y0"])
        norm_y1 = float(geometry.norm_bbox["y1"])

        independent_conditions: dict[str, Callable[[], bool]] = {
            "page_number": lambda: cls._is_page_number(text),
            "header_footer": lambda: norm_y0 <= 0.08 or norm_y1 >= 0.92,
        }
        independent_actions: dict[str, Callable[[], None]] = {
            "page_number": lambda: detected.page_numbers.append(
                PageNumberElement(
                    page_number=page_number,
                    reading_order_index=current_render_index,
                    geometry=geometry,
                    text=text,
                )
            ),
            "header_footer": lambda: detected.headers_footers.append(
                HeaderFooterElement(
                    page_number=page_number,
                    reading_order_index=current_render_index,
                    geometry=geometry,
                    text=text,
                    region="header" if norm_y0 <= 0.08 else "footer",
                )
            ),
        }
        for condition_name, condition in independent_conditions.items():
            condition() and independent_actions[condition_name]()

        classification_conditions: dict[str, Callable[[], bool]] = {
            "caption": lambda: cls._is_caption(text),
            "list_item": lambda: cls._is_list_item(text),
            "code_block": lambda: cls._is_monospace(font_names),
            "heading": lambda: max_font_size >= (body_font_size * 1.25),
        }
        classification_actions: dict[str, Callable[[], None]] = {
            "caption": lambda: detected.captions.append(
                CaptionElement(
                    page_number=page_number,
                    reading_order_index=current_render_index,
                    geometry=geometry,
                    text=text,
                )
            ),
            "list_item": lambda: detected.list_items.append(
                ListItemElement(
                    page_number=page_number,
                    reading_order_index=current_render_index,
                    geometry=geometry,
                    text=text,
                )
            ),
            "code_block": lambda: detected.code_blocks.append(
                CodeBlockElement(
                    page_number=page_number,
                    reading_order_index=current_render_index,
                    geometry=geometry,
                    text=text,
                )
            ),
            "heading": lambda: detected.headings.append(
                HeadingElement(
                    page_number=page_number,
                    reading_order_index=current_render_index,
                    geometry=geometry,
                    text=text,
                    font_size=max_font_size,
                )
            ),
            "default": lambda: detected.paragraphs.append(
                ParagraphElement(
                    page_number=page_number,
                    reading_order_index=current_render_index,
                    geometry=geometry,
                    text=text,
                )
            ),
        }

        matched_rule_name = next(
            (rule_name for rule_name, condition in classification_conditions.items() if condition()),
            "default",
        )
        classification_actions[matched_rule_name]()

        cls.render_order_index += 1

    @classmethod
    def _detect_blocks(
        cls,
        page_dict: dict[str, Any],
        detected: PageElements,
        page_number: int,
        page_width: float,
        page_height: float,
        body_font_size: float,
    ) -> None:
        for block in page_dict.get("blocks", []):
            block_type = int(block.get("type", -1))
            bbox = cls._to_bbox_tuple(block.get("bbox", (0.0, 0.0, 0.0, 0.0)))

            routing_conditions: dict[str, Callable[[], bool]] = {
                "image": lambda: block_type == 1,
                "non_image": lambda: block_type == 0,
            }
            routing_actions: dict[str, Callable[[], None]] = {
                "image": lambda: cls._append_image_block(
                    detected=detected,
                    page_number=page_number,
                    page_width=page_width,
                    page_height=page_height,
                    bbox=bbox,
                ),
                "non_image": lambda: cls._append_non_image_block(
                    block=block,
                    detected=detected,
                    page_number=page_number,
                    page_width=page_width,
                    page_height=page_height,
                    body_font_size=body_font_size,
                ),
            }

            matched_route = next((route_name for route_name, condition in routing_conditions.items() if condition()), None)
            if matched_route is None:
                cls.render_order_index += 1
                continue

            routing_actions[matched_route]()

    @classmethod
    def _detect_drawings(
        cls,
        page: fitz.Page,
        detected: PageElements,
        page_number: int,
        page_width: float,
        page_height: float,
    ) -> None:
        for drawing in page.get_drawings():
            drawing_rect = drawing.get("rect", fitz.Rect(0, 0, 0, 0))
            drawing_bbox = cls._to_bbox_tuple(drawing_rect)
            detected.drawings.append(
                DrawingElement(
                    page_number=page_number,
                    reading_order_index=cls._next_render_order_index(),
                    geometry=Geometry.from_bbox(drawing_bbox, page_width=page_width, page_height=page_height),
                    item_count=len(drawing.get("items", [])),
                )
            )

    @classmethod
    def _detect_tables(
        cls,
        page: fitz.Page,
        detected: PageElements,
        page_number: int,
        page_width: float,
        page_height: float,
    ) -> None:
        tables = page.find_tables()
        if tables is None:
            return

        for table in tables.tables:
            table_bbox = cls._to_bbox_tuple(getattr(table, "bbox", (0.0, 0.0, 0.0, 0.0)))
            row_count = len(getattr(table, "rows", []) or [])
            column_count = (
                len(getattr(table, "col_count", []) or [])
                if isinstance(getattr(table, "col_count", None), list)
                else int(getattr(table, "col_count", 0) or 0)
            )
            detected.tables.append(
                TableElement(
                    page_number=page_number,
                    reading_order_index=cls._next_render_order_index(),
                    geometry=Geometry.from_bbox(table_bbox, page_width=page_width, page_height=page_height),
                    row_count=row_count,
                    column_count=column_count,
                )
            )

    @classmethod
    def _detect_links(
        cls,
        page: fitz.Page,
        detected: PageElements,
        page_number: int,
        page_width: float,
        page_height: float,
    ) -> None:
        for link in page.get_links():
            link_bbox = cls._to_bbox_tuple(link.get("from", (0.0, 0.0, 0.0, 0.0)))
            detected.links.append(
                LinkElement(
                    page_number=page_number,
                    reading_order_index=cls._next_render_order_index(),
                    geometry=Geometry.from_bbox(link_bbox, page_width=page_width, page_height=page_height),
                    uri=link.get("uri"),
                    destination_page=link.get("page"),
                )
            )

    @classmethod
    def _detect_annotations(
        cls,
        page: fitz.Page,
        detected: PageElements,
        page_number: int,
        page_width: float,
        page_height: float,
    ) -> None:
        annotation = page.first_annot
        while annotation is not None:
            annotation_rect = cls._to_bbox_tuple(annotation.rect)
            detected.annotations.append(
                AnnotationElement(
                    page_number=page_number,
                    reading_order_index=cls._next_render_order_index(),
                    geometry=Geometry.from_bbox(annotation_rect, page_width=page_width, page_height=page_height),
                    kind=str(annotation.type[1]),
                    content=annotation.info.get("content") if annotation.info else None,
                )
            )
            annotation = annotation.next

    @classmethod
    def detect_elements(cls, page: fitz.Page) -> PageElements:
        page_number = page.number + 1
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        detected = cls(page_number=page_number, page_width=page_width, page_height=page_height)

        page_dict = page.get_text("dict")
        text_blocks = [block for block in page_dict.get("blocks", []) if int(block.get("type", -1)) == 0]
        all_sizes = [size for block in text_blocks for size in cls._block_font_sizes(block) if size > 0]
        body_font_size = median(all_sizes) if all_sizes else 10.0

        cls._detect_blocks(
            page_dict=page_dict,
            detected=detected,
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
            body_font_size=body_font_size,
        )
        cls._detect_drawings(
            page=page,
            detected=detected,
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
        )
        cls._detect_tables(
            page=page,
            detected=detected,
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
        )
        cls._detect_links(
            page=page,
            detected=detected,
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
        )
        cls._detect_annotations(
            page=page,
            detected=detected,
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
        )

        return detected

    def get_page_elements(self) -> dict[str, Any]:
        return self.get_page_details()

    def get_page_details(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "headings": [asdict(item) for item in self.headings],
            "paragraphs": [asdict(item) for item in self.paragraphs],
            "list_items": [asdict(item) for item in self.list_items],
            "captions": [asdict(item) for item in self.captions],
            "code_blocks": [asdict(item) for item in self.code_blocks],
            "images": [asdict(item) for item in self.images],
            "tables": [asdict(item) for item in self.tables],
            "drawings": [asdict(item) for item in self.drawings],
            "links": [asdict(item) for item in self.links],
            "annotations": [asdict(item) for item in self.annotations],
            "headers_footers": [asdict(item) for item in self.headers_footers],
            "page_numbers": [asdict(item) for item in self.page_numbers],
        }
