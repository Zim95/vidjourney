from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, fallback: str) -> str:
	return os.getenv(name, fallback)


def _path_env(name: str, fallback: str) -> Path:
	return Path(_env(name, fallback))


def _int_env(name: str, fallback: int) -> int:
	try:
		return max(1, int(_env(name, str(fallback))))
	except (TypeError, ValueError):
		return fallback


ENV_FILE = _path_env("ENV_FILE", "manim.env")
SCENES_DIR = _path_env("SCENES_DIR", "pipeline/scenes")
RENDER_DIR = _path_env("RENDER_DIR", "pipeline/render")
MAX_WORKERS = _int_env("MAX_WORKERS", 4)

DSL_SCENE_FILE = _path_env("DSL_SCENE_FILE", str(SCENES_DIR / "dsl_instructions.scene"))
DSL_GRAMMAR_FILE = _path_env("DSL_GRAMMAR_FILE", "src/dsl/renderer_dsl.lark")
RENDERER_INSTRUCTIONS_FILE = _path_env("RENDERER_INSTRUCTIONS_FILE", str(RENDER_DIR / "dsl_instructions.render.json"))

MANIM_SCENE_FILE = _env("MANIM_SCENE_FILE", "src/renderer/manim/manim_runner.py")
MANIM_SCENE_CLASS = _env("MANIM_SCENE_CLASS", "ManimScene")
MANIM_QUALITY = _env("MANIM_QUALITY", "ql")
MANIM_PREVIEW = _env("MANIM_PREVIEW", "true")
MANIM_VENV_PYTHON = _path_env("MANIM_VENV_PYTHON", ".venv/bin/python")
