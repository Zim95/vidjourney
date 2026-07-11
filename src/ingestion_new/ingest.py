"""
Docling-based ingestion stage — the ML-free replacement for src.ingestion.

    python -m src.ingestion_new.ingest /path/to/book.pdf

Parses the PDF with Docling, groups items into heading-delimited sections,
lets you interactively pick which sections to keep (drops front/back matter),
and writes the ``pipeline/sections/section_N.txt`` contract + resource PNGs.
Everything downstream (grouping → render → …) is unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.config.constants import GROUPING_SECTIONS_DIR
from src.stage_cli import Stage, run_stage
from src.ingestion_new.extract import extract
from src.ingestion_new.build import document_to_sections, write_sections


def _parse_ranges(text: str) -> list[tuple[int, int]] | None:
    """`15-283` / `15-283, 300-310` / `15` → [(a, b), ...]; None if unparseable."""
    text = text.strip()
    if not text:
        return None
    out: list[tuple[int, int]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, _, b = part.partition("-")
                out.append((int(a), int(b)))
            else:
                out.append((int(part), int(part)))
        except ValueError:
            return None
    return out or None


def _select(sections):
    """Display sections and interactively keep ranges (TTY only; else keep all)."""
    print(f"\nDetected {len(sections)} sections:\n")
    for i, s in enumerate(sections, 1):
        heading = s.heading or "(no heading)"
        print(f"  {i:>4}  p{s.page:<4} {heading[:72]}")

    if not sys.stdin.isatty():
        print(f"[select] non-interactive — keeping all {len(sections)} sections.")
        return sections

    ranges = _parse_ranges(input("\nSections to keep — e.g. 15-283 (Enter = keep all): "))
    if not ranges:
        return sections

    keep: set[int] = set()
    for a, b in ranges:
        for n in range(max(1, min(a, b)), min(len(sections), max(a, b)) + 1):
            keep.add(n - 1)
    selected = [s for idx, s in enumerate(sections) if idx in keep]
    if not selected:
        print("No valid ranges — keeping all.")
        return sections
    print(f"Selected {len(selected)} of {len(sections)} sections.")
    return selected


def ingest(pdf_path: Path) -> None:
    print(f"Parsing {pdf_path} with Docling …")
    doc = extract(pdf_path)
    sections = document_to_sections(doc)
    sections = _select(sections)
    written = write_sections(sections)
    print(f"Wrote {len(written)} section files to {GROUPING_SECTIONS_DIR}")


STAGE = Stage(
    name="ingestion_new.ingest",
    process_one=ingest,
    parse_item=Path,
    supports_all=False,
    supports_watch=False,
    pool="cpu",
)


if __name__ == "__main__":
    run_stage(STAGE)
