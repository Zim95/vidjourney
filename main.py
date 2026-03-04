from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from config.constants import (
    DSL_GRAMMAR_FILE,
    DSL_SCENE_FILE,
    ENV_FILE,
    MANIM_PREVIEW,
    MANIM_QUALITY,
    MANIM_SCENE_CLASS,
    MANIM_SCENE_FILE,
    MANIM_VENV_PYTHON,
    RENDERER_INSTRUCTIONS_FILE,
)
from dsl.parser import parse_scene
from dsl.transformer import SceneModelTransformer


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


def run_manim_runner() -> None:
    config = _load_env_config(ENV_FILE)
    default_python = {True: str(MANIM_VENV_PYTHON), False: sys.executable}[MANIM_VENV_PYTHON.exists()]
    manim_python = config.get("MANIM_PYTHON", default_python)
    scene_file = config.get("MANIM_SCENE_FILE", MANIM_SCENE_FILE)
    scene_class = config.get("MANIM_SCENE_CLASS", MANIM_SCENE_CLASS)
    quality = config.get("MANIM_QUALITY", MANIM_QUALITY)
    preview = _to_bool(config.get("MANIM_PREVIEW", MANIM_PREVIEW), default=True)

    preview_flag_prefix = {True: "-p", False: "-"}[preview]
    command = [manim_python, "-m", "manim", f"{preview_flag_prefix}{quality}", scene_file, scene_class]
    subprocess.run(command, check=True)


def run_manim_pipeline() -> None:
    run_dsl_to_json()
    run_manim_runner()


def run_dsl_to_json() -> None:
    ast = parse_scene(scene_path=DSL_SCENE_FILE, grammar_path=DSL_GRAMMAR_FILE)
    scene_model = SceneModelTransformer().transform(ast)
    scene_model.write_json(RENDERER_INSTRUCTIONS_FILE)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automated DSL to Manim runner")
    parser.add_argument("--renderer", default="manim", choices=["manim"])
    return parser.parse_args()


def main() -> None:
    _parse_args()
    run_manim_pipeline()


if __name__ == "__main__":
    main()
