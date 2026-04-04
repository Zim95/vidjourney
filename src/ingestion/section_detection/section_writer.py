import re
import fitz
from pathlib import Path
from collections import defaultdict

from src.ingestion.page_elements import (
    AnnotationElement,
    CaptionElement,
    CodeBlockElement,
    DrawingElement,
    HeadingElement,
    ImageElement,
    LinkElement,
    ListItemElement,
    PageElement,
    ParagraphElement,
    TableElement,
)
from src.ingestion.section_detection.code_cleanup import CodeBlockFormatUtils
from src.ingestion.code_block_renderer import render_code_block
from src.ingestion.section_detection.paragraph_utils import ParagraphUtils
from src.ingestion.section_detection.table_detection import TableDetectionUtils


class SectionWriter:

    @staticmethod
    def _ensure_output_dirs(base_dir: Path) -> dict[str, Path]:
        resources_dir = base_dir / "resources"
        directories = {
            "sections": base_dir,
            "resources": resources_dir,
            "images": resources_dir / "images",
            "code_blocks": resources_dir / "code_blocks",
            "code_block_images": resources_dir / "code_block_images",
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

        # Write the raw text file (for reference/debugging)
        SectionWriter._write_resource_and_append(
            lines=[],  # don't append CODE_BLOCK to section lines
            directories=directories,
            resource_counters=resource_counters,
            section_number=section_number,
            page_number=page_number,
            resource_name="code_blocks",
            extension="txt",
            content=formatted_code,
            line_prefix="CODE_BLOCK",
        )

        # Render to image and append IMAGE to section lines
        idx = resource_counters[(page_number, "code_blocks")]
        txt_path = SectionWriter._resource_path_for(
            directories=directories,
            section_number=section_number,
            page_number=page_number,
            resource_name="code_blocks",
            extension="txt",
            index=idx,
        )
        image_path = SectionWriter._resource_path_for(
            directories=directories,
            section_number=section_number,
            page_number=page_number,
            resource_name="code_block_images",
            extension="png",
            index=idx,
        )
        try:
            render_code_block(txt_path, image_path)
            lines.append(f"IMAGE {image_path.as_posix()}")
        except Exception:
            # fallback to text reference if rendering fails
            lines.append(f"CODE_BLOCK {txt_path.as_posix()}")

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
