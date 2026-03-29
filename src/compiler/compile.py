"""Compile pipeline: takes scene groups and produces .scene DSL files."""

from pathlib import Path

from src.compiler.scene_compiler import SceneCompiler


def compile(
    scene_groups_dir: Path = Path("pipeline") / "scene_groups",
    output_dir: Path = Path("pipeline") / "scenes",
) -> None:
    """Compile all scene groups into .scene DSL files."""
    SceneCompiler.compile_all(scene_groups_dir=scene_groups_dir, output_dir=output_dir)
