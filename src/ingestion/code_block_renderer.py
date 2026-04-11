"""
Renders code block text files to syntax-highlighted PNG images using Pygments + Pillow.

Input:  pipeline/sections/resources/code_blocks/*.txt
Output: pipeline/sections/resources/code_block_images/*.png

Skips files that already have a corresponding image.
"""
from pathlib import Path

from pygments import highlight
from pygments.lexers import guess_lexer, TextLexer
from pygments.formatters import ImageFormatter
from pygments.util import ClassNotFound

from src.config.constants import (
    INGESTION_CODE_BLOCKS_DIR,
    INGESTION_CODE_BLOCK_IMAGES_DIR,
    INGESTION_CODE_BLOCK_FONT_SIZE,
    INGESTION_CODE_BLOCK_LINE_NUMBERS,
    INGESTION_CODE_BLOCK_STYLE,
    INGESTION_CODE_BLOCK_IMAGE_PAD,
)


def _guess_lexer_safe(code: str):
    try:
        return guess_lexer(code)
    except ClassNotFound:
        return TextLexer()


def render_code_block(code_file: Path, output_file: Path) -> None:
    """Render a single code block text file to a PNG image."""
    code = code_file.read_text(encoding="utf-8", errors="replace")
    if not code.strip():
        return

    lexer = _guess_lexer_safe(code)
    formatter = ImageFormatter(
        style=INGESTION_CODE_BLOCK_STYLE,
        font_size=INGESTION_CODE_BLOCK_FONT_SIZE,
        line_numbers=INGESTION_CODE_BLOCK_LINE_NUMBERS,
        image_pad=INGESTION_CODE_BLOCK_IMAGE_PAD,
    )

    image_bytes = highlight(code, lexer, formatter)
    output_file.write_bytes(image_bytes)


def render_all_code_blocks() -> None:
    """Render all code block text files that don't already have images."""
    code_blocks_dir = INGESTION_CODE_BLOCKS_DIR
    images_dir = INGESTION_CODE_BLOCK_IMAGES_DIR
    images_dir.mkdir(parents=True, exist_ok=True)

    code_files = sorted(code_blocks_dir.glob("*.txt"))
    if not code_files:
        print("No code block files found.")
        return

    rendered = 0
    for code_file in code_files:
        output_file = images_dir / f"{code_file.stem}.png"
        if output_file.exists():
            continue

        try:
            render_code_block(code_file, output_file)
            rendered += 1
        except Exception as e:
            print(f"  FAILED: {code_file.name} — {e}")

    print(f"Rendered {rendered} code block image(s).")
