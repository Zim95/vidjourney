from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.config.constants import (
    DSL_GRAMMAR_FILE,
    MANIM_PYTHON,
    MANIM_PREVIEW,
    MANIM_QUALITY,
    MANIM_SCENE_CLASS,
    MANIM_SCENE_FILE,
    RENDER_DIR,
    RENDER_TO_MANIM_MAX_WORKERS,
    SCENES_DIR,
    SCENE_TO_RENDER_MAX_WORKERS,
)
from src.dsl.parser import parse_scene
from src.dsl.transformer import SceneModelTransformer


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


def dsl_to_json() -> None:
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)

    scene_files = sorted(SCENES_DIR.glob("*.scene"))
    if not scene_files:
        return

    max_workers = min(SCENE_TO_RENDER_MAX_WORKERS, len(scene_files))

    def _compile_scene(scene_file: Path) -> None:
        ast = parse_scene(scene_path=scene_file, grammar_path=DSL_GRAMMAR_FILE)
        scene_model = SceneModelTransformer().transform(ast)
        output_path = RENDER_DIR / f"{scene_file.stem}.render.json"
        scene_model.write_json(output_path)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_compile_scene, scene_file) for scene_file in scene_files]
        for future in futures:
            future.result()


def run_manim_runner() -> None:
    scene_file = MANIM_SCENE_FILE
    scene_class = MANIM_SCENE_CLASS
    quality = MANIM_QUALITY
    preview = _to_bool(MANIM_PREVIEW, default=True)
    python_path_exists = Path(MANIM_PYTHON).exists()
    python_resolvable = shutil.which(MANIM_PYTHON) is not None
    manim_python = MANIM_PYTHON if (python_path_exists or python_resolvable) else sys.executable
    render_files = sorted(RENDER_DIR.glob("*.render.json"))

    if not render_files:
        return

    preview_flag_prefix = {True: "-p", False: "-"}[preview]
    max_workers = min(RENDER_TO_MANIM_MAX_WORKERS, len(render_files))

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
        process_env["RENDERER_INSTRUCTIONS_FILE"] = str(render_file)
        subprocess.run(command, check=True, env=process_env)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_render_file, render_file) for render_file in render_files]
        for future in futures:
            future.result()


def run_pipeline(renderer: str) -> None:
    dsl_to_json()
    runners = {
        "manim": run_manim_runner,
    }
    run_renderer = runners.get(renderer)
    if run_renderer is None:
        raise ValueError(f"Unsupported renderer: {renderer}")
    run_renderer()
