from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable

DEFAULT_ENV_FILE = Path("manim.env")


def _load_env_config(env_file: Path) -> dict[str, str]:
    lines = env_file.read_text(encoding="utf-8").splitlines()
    parsed_entries = [line.split("=", 1) for line in lines if line.strip() and not line.strip().startswith("#") and "=" in line]
    return {key.strip(): value.strip() for key, value in parsed_entries}


def _to_bool(value: str, default: bool) -> bool:
    normalized = str(value).strip().lower()
    token_map = {
        "1": True,
        "true": True,
        "yes": True,
        "y": True,
        "on": True,
        "0": False,
        "false": False,
        "no": False,
        "n": False,
        "off": False,
    }
    return token_map.get(normalized, default)


def run_manim_runner(env_file: Path) -> None:
    config = _load_env_config(env_file)
    venv_python = Path(".venv/bin/python")
    default_python = {True: str(venv_python), False: sys.executable}[venv_python.exists()]
    manim_python = config.get("MANIM_PYTHON", default_python)
    scene_file = config.get("MANIM_SCENE_FILE", "renderer/manim/manim_runner.py")
    scene_class = config.get("MANIM_SCENE_CLASS", "ManimScene")
    quality = config.get("MANIM_QUALITY", "ql")
    preview = _to_bool(config.get("MANIM_PREVIEW", "true"), default=True)

    preview_flag_prefix = {True: "-p", False: "-"}[preview]
    command = [manim_python, "-m", "manim", f"{preview_flag_prefix}{quality}", scene_file, scene_class]
    subprocess.run(command, check=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified renderer runner")
    parser.add_argument("--renderer", default="manim", choices=["manim"])
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    runners: dict[str, Callable[[Path], None]] = {
        "manim": run_manim_runner,
    }
    runner = runners.get(args.renderer, run_manim_runner)
    runner(Path(args.env_file))


if __name__ == "__main__":
    main()
