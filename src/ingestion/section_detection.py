'''
Here we need to detect sections.
The input for this module is:
[
    (page_number, PageElements),
    ...
]
'''

import re
import fitz
from pathlib import Path
from collections import defaultdict
from ast import literal_eval
from dataclasses import dataclass, field, replace

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
        user_input = input(
            "Enter section ranges to keep as [(start, end), ...] (press Enter to keep all): "
        ).strip()

        if not user_input:
            return sections

        try:
            parsed_ranges = literal_eval(user_input)
        except (ValueError, SyntaxError):
            print("Invalid range format. Keeping all sections.")
            return sections

        if not isinstance(parsed_ranges, list):
            print("Input must be a list of tuples. Keeping all sections.")
            return sections

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
            ParagraphElement: lambda elem: lines.append(f"PARAGRAPH {elem.text}"),
            ListItemElement: lambda elem: lines.append(f"LIST_ITEM {elem.text}"),
            CaptionElement: lambda elem: lines.append(f"CAPTION {elem.text}"),
            LinkElement: lambda elem: lines.append(f"LINK uri={elem.uri} destination_page={elem.destination_page}"),
            AnnotationElement: lambda elem: lines.append(f"ANNOTATION kind={elem.kind} content={elem.content}"),
        }

        resource_handlers = {
            CodeBlockElement: lambda elem: SectionWriter._write_resource_and_append(
                lines=lines,
                directories=directories,
                resource_counters=resource_counters,
                section_number=section_number,
                page_number=page_number,
                resource_name="code_blocks",
                extension="txt",
                content=elem.text,
                line_prefix="CODE_BLOCK",
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
            TableElement: lambda elem: SectionWriter._write_resource_and_append(
                lines=lines,
                directories=directories,
                resource_counters=resource_counters,
                section_number=section_number,
                page_number=page_number,
                resource_name="tables",
                extension="txt",
                content=(
                    f"row_count={elem.row_count}\n"
                    f"column_count={elem.column_count}\n"
                    f"bbox={elem.geometry.bbox}\n"
                    f"norm_bbox={elem.geometry.norm_bbox}\n"
                ),
                line_prefix="TABLE",
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