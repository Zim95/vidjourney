from __future__ import annotations

import argparse
from src.pipeline import run_pipeline


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automated DSL to Manim runner")
    parser.add_argument("--renderer", default="manim", choices=["manim"])
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_pipeline(args.renderer)


if __name__ == "__main__":
    main()
