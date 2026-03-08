from __future__ import annotations

# built-ins
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# third party
import fitz

# local
from src.ingestion.section_detection import SectionUtils, Sections, SectionWriter
from src.config.constants import INGEST_MAX_WORKERS, INGEST_GLOBAL_READING_ORDER_STRIDE
from src.ingestion.page_elements import PageElement, PageElements
from src.utils import timer


@timer(label="Ingest PDF")
def open_document(pdf_path: Path) -> fitz.Document:
    '''
    Read the PDF document using fitz and return the fitz.Document object.
    '''
    if not pdf_path.exists() or not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    return fitz.open(pdf_path)


@timer(label="Build page chunks")
def build_page_chunks(total_pages: int, pages_per_chunk: int) -> list[tuple[int, int]]:
    '''
    Divide the pages into chunks: (start_page, end_page) tuples.
    We return a list of these tuples.
    '''
    if total_pages <= 0:
        return []

    safe_chunk_size = max(1, pages_per_chunk)  # we have atleast one page per chunk.

    return [
        (
            start_page,
            min(total_pages, start_page + safe_chunk_size - 1)
        )
        for start_page in range(1, total_pages + 1, safe_chunk_size)
    ]


@timer(label="Read page chunk")
def get_page_elements(page: fitz.Page) -> PageElements:
    return PageElements.detect_elements(page)


@timer(label="Read Page Chunk. Page Elements")
def read_page_chunk(pdf_path: Path, start_page: int, end_page: int) -> list[tuple[int, PageElements]]:
    '''
    Read a chunk of pages from start_page to end_page (inclusive).
    Each page is represented as a tuple: (page_number, page_elements).
    '''
    chunk_elements: list[tuple[int, PageElements]] = []

    with open_document(pdf_path) as document:
        total_pages = len(document)
        if start_page < 1 or end_page < start_page or end_page > total_pages:
            raise ValueError(f"Invalid page range: {start_page}..{end_page} for document with {total_pages} pages")

        for page_number in range(start_page, end_page + 1):
            page = document.load_page(page_number - 1)  # fitz is 0-based
            page_elements = get_page_elements(page)
            chunk_elements.append((page_number, page_elements))

    return chunk_elements


@timer(label="Convert to global reading order")
def convert_to_global_reading_order(
    chunk_elements: list[tuple[int, PageElements]],
    stride: int = INGEST_GLOBAL_READING_ORDER_STRIDE,
) -> list[tuple[int, PageElements]]:
    '''
    Convert page-local reading order into one global order for the whole PDF.

    - Each page starts its own reading order from 0.
    - If we combine pages directly, page 2 can also have index 0, 1, 2...
        which overlaps with page 1.
    - Stride is a fixed gap we reserve for each page.
    - Example with stride = 100000:
        - page 1 uses 0..99999
        - page 2 uses 100000..199999
        - page 3 uses 200000..299999
    - This guarantees later pages always have larger reading-order values
        than earlier pages.
    '''
    return [
        (page_number, page_elements.apply_reading_order_base((max(1, page_number) - 1) * max(1, stride)))
        for page_number, page_elements in chunk_elements
    ]


@timer(label="Ingestion: Overall time taken")
def ingest(pdf_path: Path) -> None:
    with open_document(pdf_path) as document:
        total_pages = len(document)

    pages_per_chunk = max(1, total_pages // INGEST_MAX_WORKERS)
    page_chunks = build_page_chunks(total_pages=total_pages, pages_per_chunk=pages_per_chunk)

    print(f"Total pages: {total_pages}, Pages per chunk: {pages_per_chunk}, Total chunks: {len(page_chunks)}")

    with ProcessPoolExecutor(max_workers=INGEST_MAX_WORKERS) as executor:
        futures = [
            executor.submit(read_page_chunk, pdf_path, start_page, end_page)
            for start_page, end_page in page_chunks
        ]

        all_page_elements: list[tuple[int, PageElements]] = []

        for future in futures:
            try:
                chunk_elements = convert_to_global_reading_order(future.result())
                all_page_elements.extend(chunk_elements)
            except Exception as e:
                print(f"Error processing chunk: {e}")

        all_page_elements.sort(key=lambda page_data: page_data[0])
        total_pages = all_page_elements[-1][0] if all_page_elements else 0 # read the last page number from the sorted list to get the total pages with elements.

    sections: list[list[tuple[int, PageElement]]] = Sections(page_elements=all_page_elements).detect_sections()
    filtered_sections: list[list[tuple[int, PageElement]]] = SectionUtils.filter_sections(sections)
    precleaned_sections: list[list[tuple[int, PageElement]]] = SectionUtils.preclean_sections(filtered_sections)
    noise_removed_sections: list[list[tuple[int, PageElement]]] = SectionUtils.reflow_sections(precleaned_sections)
    written_section_files = SectionWriter.write_sections_to_files(noise_removed_sections)
    print(f"Wrote {len(written_section_files)} section files to pipeline/sections")
