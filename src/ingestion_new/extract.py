"""
Docling-based PDF extraction.

Replaces the homegrown PyMuPDF + heuristics + ML detection. Docling's layout
models classify each block (section_header / text / list_item / code / table /
picture / caption / footnote) and preserve reading order — the semantic layer
the old pipeline reverse-engineered by hand.

OCR is disabled (these are text-layer PDFs) for speed; picture images are
generated so figures/tables can be exported as PNGs downstream.
"""
from __future__ import annotations

from pathlib import Path


def extract(pdf_path: Path):
    """Parse a PDF into a Docling document."""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    opts = PdfPipelineOptions()
    opts.do_ocr = False                    # text-layer PDF → skip OCR (much faster)
    opts.generate_picture_images = True    # keep figure/table crops for export
    opts.images_scale = 2.0                # 2x for crisper figure PNGs

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    return converter.convert(str(pdf_path)).document
