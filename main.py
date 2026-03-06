from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.config.constants import (
    DSL_GRAMMAR_FILE,
    ENV_FILE,
    MAX_WORKERS,
    MANIM_PREVIEW,
    MANIM_QUALITY,
    MANIM_SCENE_CLASS,
    MANIM_SCENE_FILE,
    MANIM_VENV_PYTHON,
    RENDER_DIR,
    SCENES_DIR,
)
from src.dsl.parser import parse_scene
from src.dsl.transformer import SceneModelTransformer


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
    render_files = sorted(RENDER_DIR.glob("*.render.json"))

    if not render_files:
        return

    preview_flag_prefix = {True: "-p", False: "-"}[preview]
    max_workers = min(MAX_WORKERS, len(render_files))

    def _render_file(render_file: Path) -> None:
        scene_name = render_file.name.removesuffix(".render.json")
        command = [
            manim_python,
            "-m",
            "manim",
            f"{preview_flag_prefix}{quality}",
            str(scene_file),
            scene_class,
            "-o",
            scene_name,
        ]
        process_env = os.environ.copy()
        process_env.update(config)
        process_env["RENDERER_INSTRUCTIONS_FILE"] = str(render_file)
        subprocess.run(command, check=True, env=process_env)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_render_file, render_file) for render_file in render_files]
        for future in futures:
            future.result()


def run_manim_pipeline() -> None:
    run_dsl_to_json()
    run_manim_runner()


def run_dsl_to_json() -> None:
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)

    scene_files = sorted(SCENES_DIR.glob("*.scene"))
    if not scene_files:
        return

    max_workers = min(MAX_WORKERS, len(scene_files))

    def _compile_scene(scene_file: Path) -> None:
        ast = parse_scene(scene_path=scene_file, grammar_path=DSL_GRAMMAR_FILE)
        scene_model = SceneModelTransformer().transform(ast)
        output_path = RENDER_DIR / f"{scene_file.stem}.render.json"
        scene_model.write_json(output_path)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_compile_scene, scene_file) for scene_file in scene_files]
        for future in futures:
            future.result()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automated DSL to Manim runner")
    parser.add_argument("--renderer", default="manim", choices=["manim"])
    return parser.parse_args()


def main() -> None:
    _parse_args()
    run_manim_pipeline()


if __name__ == "__main__":
    main()
