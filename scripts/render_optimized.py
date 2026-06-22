"""Phase-2 validator: regroup sections 1-5 with the new deterministic
grouper and render them through scroll/build_raster into an isolated
``pipeline/scroll/raster_optimized/`` tree.

Nothing in the current ``pipeline/groups/content_groups/`` or
``pipeline/scroll/output/`` is touched — the script monkey-patches the
output paths on the build_raster + build modules so every artifact lands
in a parallel ``*_optimized/`` tree. The narration cache is also a
sibling directory so we don't overwrite the locked-in raw_wav + sidecar
pairs that the current videos rely on.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Project root on sys.path so the ``src.*`` imports resolve when running
# this file directly (``python scripts/render_optimized.py``).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import logger
from src.scene_grouping.llm_grouper import group_section_file, serialize_groups

SECTIONS_TO_RENDER = [1, 2, 3, 4, 5]

SECTIONS_DIR = Path("pipeline/sections")
OPT_CONTENT_GROUPS = Path("pipeline/groups/content_groups_optimized")
OPT_SCROLL_ROOT = Path("pipeline/scroll_optimized")
OPT_NARRATION = OPT_SCROLL_ROOT / "narration"
OPT_INSTRUCTIONS = OPT_SCROLL_ROOT / "instructions"
OPT_CANVAS = OPT_SCROLL_ROOT / "canvas"
# The actual mp4s land in the directory name the user requested.
OPT_OUTPUT = Path("pipeline/scroll/raster_optimized")


def regroup(section_id: int) -> Path:
    """Run the new deterministic grouper on a section, write to the
    isolated content_groups_optimized/ directory. Always overwrites — we
    want fresh output from the current code on each invocation."""
    section_file = SECTIONS_DIR / f"section_{section_id}.txt"
    if not section_file.exists():
        raise FileNotFoundError(section_file)
    OPT_CONTENT_GROUPS.mkdir(parents=True, exist_ok=True)
    out_path = OPT_CONTENT_GROUPS / f"section_{section_id}.txt"
    groups = group_section_file(section_file)
    out_path.write_text(serialize_groups(groups), encoding="utf-8")
    logger.info(
        f"regrouped section_{section_id}: {len(groups)} groups → {out_path}"
    )
    return out_path


def _patch_paths() -> None:
    """Redirect scroll/build_raster + scroll/build path constants at every
    place they're referenced. Module-attribute assignment changes the value
    Python sees when the consumer does ``GROUPING_CONTENT_GROUPS_DIR / ...``
    at runtime — but only for names looked up via the module's own
    globals. Functions that imported a binding via ``from X import Y`` keep
    their original value, which is why we patch the modules in-place rather
    than swapping a constants module.
    """
    import src.scroll.build as build
    import src.scroll.build_raster as raster

    OPT_NARRATION.mkdir(parents=True, exist_ok=True)
    OPT_INSTRUCTIONS.mkdir(parents=True, exist_ok=True)
    OPT_CANVAS.mkdir(parents=True, exist_ok=True)
    OPT_OUTPUT.mkdir(parents=True, exist_ok=True)

    # build.py drives narration + instructions writing inside build_section.
    build.SCROLL_DIR = OPT_SCROLL_ROOT
    build.NARRATION_DIR = OPT_NARRATION
    build.INSTRUCTIONS_DIR = OPT_INSTRUCTIONS
    build.OUTPUT_DIR = OPT_OUTPUT

    # build_raster.py reads content_groups, writes canvas + final mp4.
    raster.SCROLL_DIR = OPT_SCROLL_ROOT
    raster.CANVAS_DIR = OPT_CANVAS
    raster.NARRATION_DIR = OPT_NARRATION
    raster.OUTPUT_DIR = OPT_OUTPUT
    raster.GROUPING_CONTENT_GROUPS_DIR = OPT_CONTENT_GROUPS

    logger.info("path patches in place:")
    logger.info(f"  content_groups: {raster.GROUPING_CONTENT_GROUPS_DIR}")
    logger.info(f"  narration:      {raster.NARRATION_DIR}")
    logger.info(f"  instructions:   {build.INSTRUCTIONS_DIR}")
    logger.info(f"  canvas:         {raster.CANVAS_DIR}")
    logger.info(f"  mp4 output:     {raster.OUTPUT_DIR}")


def render(section_id: int) -> Path:
    """Call build_section_raster with the patched paths."""
    # Import after patching so any module-level binding inside build_raster
    # resolves correctly. (In this module ordering, _patch_paths sets the
    # module attributes; build_section_raster reads them at call time.)
    from src.scroll.build_raster import build_section_raster
    return build_section_raster(section_id)


def _seed_narration_cache() -> None:
    """Copy the production narration cache for the sections we'll render
    into the isolated tree. The cache is content-keyed via sidecar ``.txt``
    files — blocks whose text matches will be reused; blocks whose text
    differs (the cases the deterministic grouper might produce) re-narrate
    and overwrite only in the isolated tree. Big speedup vs. starting cold.
    """
    src_root = Path("pipeline/scroll/narration")
    for sid in SECTIONS_TO_RENDER:
        section_name = f"section_{sid}"
        src = src_root / section_name
        dst = OPT_NARRATION / section_name
        if dst.exists() or not src.exists():
            continue
        shutil.copytree(src, dst)
        logger.info(f"seeded narration cache: {dst}")


def main() -> None:
    _patch_paths()
    _seed_narration_cache()

    logger.info("=== phase 2: regrouping sections with the new grouper ===")
    for sid in SECTIONS_TO_RENDER:
        regroup(sid)

    logger.info("=== phase 2: rendering optimized mp4s ===")
    rendered: list[Path] = []
    for sid in SECTIONS_TO_RENDER:
        mp4 = render(sid)
        rendered.append(mp4)
        logger.info(f"rendered: {mp4}")

    print()
    print("=== summary ===")
    for mp4 in rendered:
        print(f"  {mp4}")
    print()
    print(f"compare against current:    pipeline/scroll/output/section_*_raster.mp4")
    print(f"compare against new code:   {OPT_OUTPUT}/section_*_raster.mp4")
    print(f"new content_groups:         {OPT_CONTENT_GROUPS}/section_*.txt")
    print(f"old content_groups (kept):  pipeline/groups/content_groups/section_*.txt")


if __name__ == "__main__":
    main()
