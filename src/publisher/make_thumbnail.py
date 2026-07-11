"""
Thumbnail prep: turn any source image into a YouTube-spec 1280x720 (16:9) PNG.

YouTube wants thumbnails at 1280x720, 16:9, JPG/PNG, under 2 MB. This takes an
arbitrary image (any size/aspect) and produces that, upscaling small sources as
needed. Point ``[youtube] thumbnail_file`` at the output.

    python -m src.publisher.make_thumbnail source.png                # -> source_16x9.png
    python -m src.publisher.make_thumbnail source.png -o thumb.png
    python -m src.publisher.make_thumbnail source.png --fit contain   # letterbox, no crop
    python -m src.publisher.make_thumbnail source.png --fit contain --bg white

Fit modes:
- ``cover``   (default) — scale to fill, then center-crop to 16:9. Full-bleed,
  edges may be trimmed. What most thumbnails want.
- ``contain`` — scale to fit inside 16:9, then pad the remainder (letterbox).
  Keeps the whole image; adds bars in ``--bg``.
"""
from __future__ import annotations

import argparse
from pathlib import Path

WIDTH, HEIGHT = 1280, 720
MAX_BYTES = 2 * 1024 * 1024  # YouTube's 2 MB thumbnail limit


def make_thumbnail(
    src: Path,
    out: Path | None = None,
    fit: str = "cover",
    bg: str = "black",
) -> Path:
    """Write a 1280x720 PNG from ``src`` and return its path."""
    from PIL import Image

    if out is None:
        out = src.with_name(f"{src.stem}_16x9.png")

    img = Image.open(src).convert("RGB")
    w, h = img.size

    if fit == "cover":
        scale = max(WIDTH / w, HEIGHT / h)
        resized = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        rw, rh = resized.size
        left = (rw - WIDTH) // 2
        top = (rh - HEIGHT) // 2
        canvas = resized.crop((left, top, left + WIDTH, top + HEIGHT))
    elif fit == "contain":
        scale = min(WIDTH / w, HEIGHT / h)
        resized = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        canvas = Image.new("RGB", (WIDTH, HEIGHT), bg)
        canvas.paste(resized, ((WIDTH - resized.width) // 2, (HEIGHT - resized.height) // 2))
    else:
        raise ValueError(f"--fit must be 'cover' or 'contain', got {fit!r}")

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG", optimize=True)

    size = out.stat().st_size
    note = ""
    if size > MAX_BYTES:
        note = f"  ⚠ {size/1_048_576:.1f} MB > 2 MB limit — save a JPG or simplify the image"
    print(f"Wrote {out}  ({WIDTH}x{HEIGHT}, {size/1024:.0f} KB){note}")
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Make a 1280x720 (16:9) YouTube thumbnail")
    ap.add_argument("source", type=Path, help="source image (any size/aspect)")
    ap.add_argument("-o", "--output", type=Path, default=None, help="output path (default: <source>_16x9.png)")
    ap.add_argument("--fit", choices=["cover", "contain"], default="cover",
                    help="cover = crop to fill (default); contain = pad/letterbox")
    ap.add_argument("--bg", default="black", help="letterbox color for --fit contain (default black)")
    args = ap.parse_args(argv)

    if not args.source.exists():
        raise SystemExit(f"source not found: {args.source}")
    make_thumbnail(args.source, args.output, fit=args.fit, bg=args.bg)


if __name__ == "__main__":
    main()
