"""
Map a Docling document into the section-file contract the rest of the pipeline
consumes (``pipeline/sections/section_N.txt`` + ``resources/``).

Docling label → element:
    section_header / title  -> HEADING  (starts a new section)
    text / paragraph        -> PARAGRAPH
    list_item               -> LIST_ITEM
    caption                 -> CAPTION
    code                    -> CODE_BLOCK   (code text rendered to a PNG)
    picture                 -> IMAGE        (figure crop exported to a PNG)
    table                   -> TABLE        (table crop exported to a PNG)
    footnote / page_* / ... -> dropped (narration noise)

Everything visual (code, figures, tables) becomes a PNG referenced by path —
which is exactly how the render stage shows resources.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.config.constants import GROUPING_SECTIONS_DIR
from src.ingestion.code_block_renderer import render_code_block


@dataclass
class Item:
    kind: str            # PARAGRAPH | LIST_ITEM | CAPTION | CODE | PICTURE | TABLE
    text: str = ""       # text for text kinds; source code for CODE
    image: object = None # PIL image for PICTURE / TABLE


@dataclass
class Section:
    heading: str
    page: int
    items: list[Item] = field(default_factory=list)


def _label(item) -> str:
    return str(getattr(item, "label", "")).split(".")[-1].lower()


def _page(item) -> int:
    prov = getattr(item, "prov", None)
    return prov[0].page_no if prov else 1


def _trivial(text: str) -> bool:
    t = text.strip()
    return len(t) < 2 or not any(c.isalnum() for c in t)


def _safe_image(item, doc):
    try:
        return item.get_image(doc)
    except Exception:
        return None


def document_to_sections(doc) -> list[Section]:
    """Walk the Docling document in reading order and group into sections."""
    sections: list[Section] = []
    current: Section | None = None

    def ensure(page: int) -> Section:
        nonlocal current
        if current is None:
            current = Section(heading="", page=page)
            sections.append(current)
        return current

    for item, _level in doc.iterate_items():
        lab = _label(item)
        page = _page(item)
        text = (getattr(item, "text", "") or "").strip()

        if lab in ("section_header", "title"):
            current = Section(heading=text, page=page)
            sections.append(current)
        elif lab in ("text", "paragraph"):
            if not _trivial(text):
                ensure(page).items.append(Item("PARAGRAPH", text=text))
        elif lab == "list_item":
            if text:
                ensure(page).items.append(Item("LIST_ITEM", text=text))
        elif lab == "caption":
            if text:
                ensure(page).items.append(Item("CAPTION", text=text))
        elif lab == "code":
            if text.strip():
                ensure(page).items.append(Item("CODE", text=text))
        elif lab == "picture":
            img = _safe_image(item, doc)
            if img is not None:
                ensure(page).items.append(Item("PICTURE", image=img))
        elif lab == "table":
            img = _safe_image(item, doc)
            if img is not None:
                ensure(page).items.append(Item("TABLE", image=img))
        # footnote / page_header / page_footer / form / etc. → dropped

    # Keep sections that carry content or at least a heading.
    return [s for s in sections if s.items or s.heading]


def write_sections(sections: list[Section], sections_dir: Path = GROUPING_SECTIONS_DIR) -> list[Path]:
    """Write section files + resource PNGs in the contract the grouper parses."""
    res = sections_dir / "resources"
    dirs = {name: res / name for name in ("images", "code_blocks", "code_block_images", "tables")}
    for d in (sections_dir, *dirs.values()):
        d.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for i, sec in enumerate(sections, 1):
        lines = [f"section_number: {i}", "", f"page_number: {sec.page}", ""]
        if sec.heading:
            lines.append(f"HEADING {sec.heading}")

        counters = {"images": 0, "code_blocks": 0, "tables": 0}
        for it in sec.items:
            if it.kind in ("PARAGRAPH", "LIST_ITEM", "CAPTION"):
                lines.append(f"{it.kind} {it.text}")
            elif it.kind == "CODE":
                counters["code_blocks"] += 1
                idx = counters["code_blocks"]
                txt = dirs["code_blocks"] / f"{i}_{sec.page}_code_blocks_{idx}.txt"
                png = dirs["code_block_images"] / f"{i}_{sec.page}_code_block_images_{idx}.png"
                txt.write_text(it.text, encoding="utf-8")
                try:
                    render_code_block(txt, png)
                    lines.append(f"CODE_BLOCK {png.as_posix()}")
                except Exception:
                    lines.append(f"CODE_BLOCK {txt.as_posix()}")
            elif it.kind == "PICTURE":
                counters["images"] += 1
                idx = counters["images"]
                png = dirs["images"] / f"{i}_{sec.page}_images_{idx}.png"
                it.image.save(png)
                lines.append(f"IMAGE {png.as_posix()}")
            elif it.kind == "TABLE":
                counters["tables"] += 1
                idx = counters["tables"]
                png = dirs["tables"] / f"{i}_{sec.page}_tables_{idx}.png"
                it.image.save(png)
                lines.append(f"TABLE {png.as_posix()}")

        out = sections_dir / f"section_{i}.txt"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(out)

    return written
