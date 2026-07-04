"""
Pre-rasterize + ffmpeg-pan build path.

Shares the upstream pipeline with :mod:`src.render.build` (content_groups
→ blocks → narration → layout → instructions.json); the only difference
is the final mp4-creation step. Instead of manim animating the camera
through 60 frames/sec of scrolling, we:

1. Render the entire section's content as a single tall PNG via manim's
   ``-s`` mode (one frame, no animation).
2. Build an ffmpeg ``crop`` expression that pans a 1080p-equivalent
   window across the PNG over the section's narration duration.
3. Use ``-vf`` to chain ``crop`` + ``scale`` + Lanczos downsample for
   sub-pixel-smooth motion (2× supersampling), then merge with the
   section narration wav.

Order-of-magnitude expectation: section 1 took ~3-5 min via manim's
animation engine; this path should land in ~30-60 seconds total because
the single-frame manim render is the only expensive step. The ffmpeg pan
is fast even at 2× supersample because it's just memory translation.

CLI:
    python -m src.render.build_raster 1
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from src.utils import logger
from src.config.constants import MANIM_PYTHON, GROUPING_APPROVED_DIR
from src.scheduler import subprocess_slot

from src.render.build import (
    _camera_path,
    _groups_to_blocks,
    _layout,
    _narrate_blocks,
    _write_instructions,
)
from src.render.canvas_scene import CANVAS_TOP_PADDING, CANVAS_BOTTOM_PADDING
from src.grouping.llm_grouper import deserialize_groups


SCROLL_DIR = Path("pipeline/scroll")
CANVAS_DIR = SCROLL_DIR / "canvas"      # tall PNGs go here
OUTPUT_DIR = SCROLL_DIR / "output"
NARRATION_DIR = SCROLL_DIR / "narration"
GROUPING_CONTENT_GROUPS_DIR = Path("pipeline/groups/content_groups")
# Render consumes the review gate's approved/ output; if a section hasn't been
# through the gate (ungated/partial run) it falls back to content_groups.
INPUT_DIR = GROUPING_APPROVED_DIR
FALLBACK_DIR = GROUPING_CONTENT_GROUPS_DIR


def _render_input(section_name: str) -> Path:
    """approved/section_N.txt if present, else content_groups/section_N.txt."""
    approved = INPUT_DIR / f"{section_name}.txt"
    return approved if approved.exists() else FALLBACK_DIR / f"{section_name}.txt"

# Supersample factor. 2× means we render the canvas PNG at 3840 wide and
# crop+downscale to 1920×1080 — the downsample is the sub-pixel smoother.
# Bump to 4× if 2× motion looks janky (memory/render cost ~4×).
SUPERSAMPLE = 2

# Manim default frame ratio: 14.22 world units wide × 8 world units tall
# renders to 1920 × 1080 px. So pixels-per-unit = 135 at 1× / 270 at 2× /
# 540 at 4×. We compute pixel canvas dimensions from these.
PIXELS_PER_UNIT_1X = 135  # 1080 / 8

# Cairo (manim's render backend) hard-fails above 32767px in any surface
# dimension — it's a signed-16-bit coordinate limit. Tall sections at 2×
# supersample blow past this (e.g. a 180-unit section → 48600px). We keep
# a margin below the hard limit and drop to a lower supersample for those
# sections so they render at all (slightly softer, but not a hard failure).
CAIRO_MAX_DIM = 32767
SAFE_CANVAS_PX = 32000


def _effective_supersample(canvas_height_units: float) -> int:
    """Pick the largest supersample (≤ SUPERSAMPLE) that keeps the canvas
    PNG height under the Cairo surface limit. Falls back through 2→1 for
    very tall sections."""
    total_world_height = (
        CANVAS_TOP_PADDING + canvas_height_units + CANVAS_BOTTOM_PADDING
    )
    for ss in range(SUPERSAMPLE, 0, -1):
        if total_world_height * PIXELS_PER_UNIT_1X * ss <= SAFE_CANVAS_PX:
            return ss
    # Even 1× exceeds the limit — return 1 and let the caller log; this
    # would need canvas tiling to fix, which no section currently needs.
    return 1


def _canvas_pixel_dims(canvas_height_units: float, supersample: int) -> tuple[int, int]:
    """Return (width, height) pixels for the tall canvas PNG at the given
    supersample factor. Includes top + bottom padding."""
    total_world_height = (
        CANVAS_TOP_PADDING + canvas_height_units + CANVAS_BOTTOM_PADDING
    )
    px_per_unit = PIXELS_PER_UNIT_1X * supersample
    pixel_width = 1920 * supersample
    pixel_height = int(round(total_world_height * px_per_unit))
    return pixel_width, pixel_height


def _world_y_to_pixel_y(camera_y_world: float, supersample: int) -> int:
    """Convert camera-center world y to pixel y of the viewport TOP in the
    canvas PNG.

    The canvas PNG's pixel y=0 corresponds to world y = ``CANVAS_TOP_PADDING``.
    The viewport is 8 world-units tall (manim default), so the top of the
    viewport is at world y = ``camera_y + 4``.
    """
    viewport_top_world_y = camera_y_world + 4.0
    px_per_unit = PIXELS_PER_UNIT_1X * supersample
    return int(round((CANVAS_TOP_PADDING - viewport_top_world_y) * px_per_unit))


# Max camera waypoints to feed the ffmpeg expression. ffmpeg's evaluator
# caps total operand count (not just nesting depth) — a flat sum of ~200+
# gated terms still fails with "too many args". We simplify the y(t) curve
# (Douglas-Peucker, vertical metric) down to this many points first; the
# tolerance is small enough that the motion is visually identical.
_MAX_CAMERA_WAYPOINTS = 80
_RDP_TOLERANCE_PX = 30.0


def _simplify_waypoints(wps: list[dict], tolerance_px: float) -> list[dict]:
    """Douglas-Peucker simplification of the y(t) curve, using *vertical*
    (py) deviation rather than perpendicular distance — y is a single-valued
    function of t, so vertical error is the meaningful metric. Keeps the
    endpoints, drops interior points whose removal moves the interpolated
    curve by less than ``tolerance_px``."""
    if len(wps) < 3:
        return wps

    keep = [False] * len(wps)
    keep[0] = keep[-1] = True
    stack = [(0, len(wps) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        t0, y0 = wps[lo]["t"], wps[lo]["py"]
        t1, y1 = wps[hi]["t"], wps[hi]["py"]
        dt = t1 - t0
        dmax, idx = -1.0, -1
        for i in range(lo + 1, hi):
            ti, yi = wps[i]["t"], wps[i]["py"]
            interp = y0 if dt <= 1e-9 else y0 + (y1 - y0) * (ti - t0) / dt
            d = abs(yi - interp)
            if d > dmax:
                dmax, idx = d, i
        if dmax > tolerance_px and idx != -1:
            keep[idx] = True
            stack.append((lo, idx))
            stack.append((idx, hi))
    return [wp for wp, k in zip(wps, keep) if k]


def _build_ffmpeg_y_expression(camera_path: list[dict], supersample: int) -> str:
    """Build an ffmpeg expression for the crop's top-left pixel y over time.

    Formulated as a FLAT SUM of gated linear terms rather than nested
    ``if()`` calls. ffmpeg's expression parser has a hard nesting-depth
    limit (~100 deep), and long sections produce 100+ camera waypoints —
    the nested form fails to parse with "Missing ')' or too many args".
    The flat sum has zero nesting depth:

        y(t) = Σ_i  gate_i(t) · (y_i + (t - t_i)·slope_i)  +  hold_tail(t)

    where ``gate_i(t) = gte(t, t_i)·lt(t, t_{i+1})`` is 1 on the half-open
    interval [t_i, t_{i+1}) and 0 elsewhere. Exactly one gate is active at
    any t (no overlap, no gap), so the sum reduces to the single active
    segment. The tail term holds the last waypoint's y for t ≥ t_last.
    """
    if not camera_path:
        return "0"
    wps = [
        {"t": float(wp["t"]), "py": _world_y_to_pixel_y(float(wp["y"]), supersample)}
        for wp in camera_path
    ]
    # Simplify the y(t) curve so the expression stays under ffmpeg's operand
    # cap. Bump the tolerance until we're under _MAX_CAMERA_WAYPOINTS — even
    # at large tolerances the visual drift is sub-second and imperceptible
    # because the pan is slow.
    tol = _RDP_TOLERANCE_PX
    while len(wps) > _MAX_CAMERA_WAYPOINTS:
        wps = _simplify_waypoints(wps, tol)
        tol *= 2
        if tol > 100000:  # pathological guard; should never trigger
            break

    terms: list[str] = []
    for i in range(len(wps) - 1):
        t0, y0 = wps[i]["t"], wps[i]["py"]
        t1, y1 = wps[i + 1]["t"], wps[i + 1]["py"]
        if t1 - t0 < 1e-3:
            continue  # zero-duration hold; neighbouring gates cover it
        slope = (y1 - y0) / (t1 - t0)
        terms.append(
            f"(gte(t\\,{t0:.3f})*lt(t\\,{t1:.3f}))*({y0:.1f}+(t-{t0:.3f})*{slope:.4f})"
        )
    # Trailing hold: for t ≥ the last waypoint, the camera stays put.
    terms.append(f"gte(t\\,{wps[-1]['t']:.3f})*{wps[-1]['py']:.1f}")
    return "+".join(terms)


def _render_canvas_png(instructions_path: Path, section_name: str, supersample: int) -> Path:
    """Run manim with ``CanvasScene`` to produce a single tall PNG containing
    the entire section content laid out. Manim's ``-s`` flag saves the last
    frame as PNG; combined with our custom ``--resolution`` we get a PNG
    sized to the supersampled canvas dimensions.
    """
    CANVAS_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(instructions_path.read_text(encoding="utf-8"))
    canvas_height = float(data["total_height"])
    pixel_width, pixel_height = _canvas_pixel_dims(canvas_height, supersample)

    canvas_png = CANVAS_DIR / f"{section_name}.png"
    if canvas_png.exists():
        canvas_png.unlink()  # always regenerate; cheap step

    env = os.environ.copy()
    env["SCROLL_INSTRUCTIONS_FILE"] = str(instructions_path.resolve())

    cmd = [
        MANIM_PYTHON, "-m", "manim",
        "-s",  # save the last frame as a still image
        f"--resolution={pixel_width},{pixel_height}",
        "-o", f"{section_name}_canvas",
        "--media_dir", "media/scroll_raster",
        "src/render/canvas_scene.py", "CanvasScene",
    ]
    logger.info(
        f"Rendering canvas PNG ({pixel_width}×{pixel_height}, "
        f"canvas_height={canvas_height:.1f}u, supersample={supersample}×): {' '.join(cmd)}"
    )
    with subprocess_slot():
        subprocess.run(cmd, env=env, check=True)

    # Manim writes to media/scroll_raster/images/canvas_scene/<name>.png
    manim_images_root = Path("media/scroll_raster/images/canvas_scene")
    found = list(manim_images_root.rglob(f"{section_name}_canvas*.png")) if manim_images_root.exists() else []
    if not found:
        # Sometimes manim picks a different filename; just grab the newest png
        all_pngs = list(manim_images_root.rglob("*.png")) if manim_images_root.exists() else []
        if not all_pngs:
            raise RuntimeError("Manim didn't produce a canvas PNG")
        found = [max(all_pngs, key=lambda p: p.stat().st_mtime)]
    found[0].rename(canvas_png)
    logger.info(f"Canvas PNG: {canvas_png} ({pixel_width}×{pixel_height})")
    return canvas_png


def _ffmpeg_pan_and_merge(
    canvas_png: Path,
    audio_wav: Path,
    camera_path: list[dict],
    total_duration: float,
    canvas_height_units: float,
    output_mp4: Path,
    supersample: int,
) -> Path:
    """Run ffmpeg: pan a supersampled viewport across the canvas PNG,
    downscale to 1920×1080 via Lanczos (sub-pixel smoother), merge with the
    section's narration wav. Output is the final mp4 for this section.

    ``supersample`` MUST match the value used to render the canvas PNG —
    the crop window and pixel-y math are derived from it.
    """
    pixel_width, pixel_height = _canvas_pixel_dims(canvas_height_units, supersample)
    viewport_pixel_width = 1920 * supersample
    viewport_pixel_height = 1080 * supersample
    max_y = pixel_height - viewport_pixel_height  # safety clamp

    y_expr = _build_ffmpeg_y_expression(camera_path, supersample)
    # Clamp to [0, max_y] so we never crop off the PNG.
    y_clamped = f"max(0\\,min({y_expr}\\,{max_y}))"

    # crop=W:H:x:y followed by scale to final 1080p with Lanczos
    vf = (
        f"crop={viewport_pixel_width}:{viewport_pixel_height}:0:{y_clamped},"
        f"scale=1920:1080:flags=lanczos,fps=60"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(canvas_png),
        "-i", str(audio_wav),
        "-t", f"{total_duration:.3f}",
        "-vf", vf,
        # Force 4:2:0 chroma subsampling. Without this libx264 picks yuv444p
        # because the PNG input is RGB; yuv444p plays back fine in ffplay
        # but stutters/freezes in QuickTime, browsers, and most mobile
        # players because they fall back to slow software decode.
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac",
        "-shortest",
        str(output_mp4),
    ]
    logger.info(f"Running ffmpeg pan: {len(camera_path)} waypoints over {total_duration:.1f}s")
    if output_mp4.exists():
        output_mp4.unlink()
    with subprocess_slot():
        subprocess.run(cmd, check=True, capture_output=True)
    logger.info(f"Raster mp4: {output_mp4}")
    return output_mp4


def build_section_raster(section_id: int) -> Path:
    section_name = f"section_{section_id}"
    cg_path = _render_input(section_name)
    if not cg_path.exists():
        # Raise (not sys.exit) so a pool/watch run reports and continues.
        raise FileNotFoundError(
            f"Missing render input for {section_name}: neither {INPUT_DIR} nor {FALLBACK_DIR}"
        )

    groups = deserialize_groups(cg_path.read_text(encoding="utf-8"))
    # trust_kinds: gated/approved content's group kinds are authoritative — the
    # review gate already decided ambiguous quotes, so skip the render fallback.
    blocks = _groups_to_blocks(groups, section_name, trust_kinds=True)
    logger.info(f"Built {len(blocks)} blocks for {section_name}")

    durations, section_wav = _narrate_blocks(blocks, section_name)
    layouts, total_height, total_audio = _layout(blocks, durations)
    camera_path = _camera_path(layouts, total_audio)
    instructions_path = _write_instructions(
        section_name, layouts, total_height, total_audio, camera_path
    )

    supersample = _effective_supersample(total_height)
    if supersample < SUPERSAMPLE:
        logger.info(
            f"{section_name}: tall canvas ({total_height:.1f}u) — dropping "
            f"supersample {SUPERSAMPLE}× → {supersample}× to stay under "
            f"Cairo's {CAIRO_MAX_DIM}px limit"
        )

    canvas_png = _render_canvas_png(instructions_path, section_name, supersample)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_mp4 = OUTPUT_DIR / f"{section_name}_raster.mp4"
    _ffmpeg_pan_and_merge(
        canvas_png=canvas_png,
        audio_wav=section_wav,
        camera_path=camera_path,
        total_duration=total_audio,
        canvas_height_units=total_height,
        output_mp4=output_mp4,
        supersample=supersample,
    )
    return output_mp4


def _pending_sections() -> list[int]:
    """Section ids whose render input exists (approved/ preferred, else
    content_groups/) but whose output mp4 is missing or stale."""
    seen: set[int] = set()
    ids: list[int] = []
    for d in (INPUT_DIR, FALLBACK_DIR):
        if not d.exists():
            continue
        for cg in sorted(d.glob("section_*.txt")):
            try:
                sid = int(cg.stem.split("_")[1])
            except (IndexError, ValueError):
                continue
            if sid in seen:
                continue
            seen.add(sid)
            src = _render_input(f"section_{sid}")
            out = OUTPUT_DIR / f"section_{sid}_raster.mp4"
            if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
                ids.append(sid)
    return sorted(ids)


def _section_id(p: Path) -> int:
    return int(p.stem.split("_")[1])


from src.stage_cli import Stage, run_stage

STAGE = Stage(
    name="render.build_raster",
    process_one=build_section_raster,     # int section id -> output/section_N_raster.mp4
    parse_item=int,
    pending=_pending_sections,
    watch_dir=INPUT_DIR,
    watch_match=lambda p: p.suffix == ".txt" and p.stem.startswith("section_"),
    item_from_event=_section_id,
    pool="io",
)


if __name__ == "__main__":
    run_stage(STAGE)
