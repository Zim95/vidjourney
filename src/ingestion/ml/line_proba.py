"""
Diagnostic script: prints per-line code probability for each code block.

Usage:
    python line_proba.py                          # all code blocks
    python line_proba.py --limit 10               # first 10 code blocks
    python line_proba.py --file <path_to_code_block_txt>  # single file
"""
import argparse
from pathlib import Path

from src.ingestion.ml.train import predict_is_code_proba


def print_line_probabilities(code_block_path: Path) -> None:
    text = code_block_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return

    print(f"\n{'='*80}")
    print(f"FILE: {code_block_path}")
    print(f"WHOLE-BLOCK PROBA: {predict_is_code_proba(text):.4f}")
    print(f"{'='*80}")
    print(f"{'LINE':>5}  {'PROBA':>7}  CONTENT")
    print(f"{'-'*5}  {'-'*7}  {'-'*60}")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            print(f"{i:>5}  {'---':>7}  (empty)")
            continue
        proba = predict_is_code_proba(stripped)
        marker = " <-- TEXT?" if proba < 0.4 else ""
        print(f"{i:>5}  {proba:>7.4f}  {stripped[:60]}{marker}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Per-line code probability diagnostic")
    parser.add_argument("--file", type=str, help="Path to a single code block file")
    parser.add_argument("--limit", type=int, default=None, help="Max number of code blocks to process")
    args = parser.parse_args()

    if args.file:
        print_line_probabilities(Path(args.file))
        return

    code_blocks_dir = Path("pipeline/sections/resources/code_blocks")
    if not code_blocks_dir.exists():
        print(f"Code blocks directory not found: {code_blocks_dir}")
        return

    files = sorted(code_blocks_dir.glob("*.txt"))
    if args.limit:
        files = files[:args.limit]

    print(f"Processing {len(files)} code block(s)...")
    for f in files:
        print_line_probabilities(f)


if __name__ == "__main__":
    # Usage:
    #   python -m src.ingestion.ml.line_proba --file pipeline/sections/resources/code_blocks/27_66_code_blocks_1.txt
    #   python -m src.ingestion.ml.line_proba --limit 10
    #   python -m src.ingestion.ml.line_proba
    main()
