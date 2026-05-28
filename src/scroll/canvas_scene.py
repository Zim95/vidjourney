"""
Manim scene that lays out an entire section's content as a single tall
canvas frame. Used by the pre-rasterize perf path — manim renders the
canvas once as a PNG, then ffmpeg pans over the PNG to produce the
scrolling video.

Inherits from :class:`ScrollScene` so the mobject factories (heading,
bullet, image, quote) and the layout math are shared verbatim. Only the
camera setup differs — instead of starting at the top and animating
downward, the camera frame is widened to encompass the entire canvas (plus
padding for the viewport's lead room) so the single rendered frame
contains everything.

Invoked by ``build_raster.py``. Manim's ``-s`` flag saves the last frame
as a PNG; we tell manim to produce 3840×N pixels where N covers the whole
canvas at 2× supersample density. The PNG is then handed to ffmpeg with a
``crop`` filter that pans across it over the section's narration duration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from src.scroll.manim_scene import ScrollScene


# How much vertical "headroom" the camera viewport needs above the canvas
# top (and below the canvas bottom) so that when the camera starts at
# ``CAMERA_LEAD`` above the first block, the viewport doesn't extend past
# the top edge of the rendered PNG. Set generously so ffmpeg's crop never
# tries to read pixels that don't exist.
CANVAS_TOP_PADDING = 6.0
CANVAS_BOTTOM_PADDING = 6.0


class CanvasScene(ScrollScene):
    def construct(self) -> None:
        instructions_path = os.environ.get("SCROLL_INSTRUCTIONS_FILE")
        if not instructions_path:
            raise RuntimeError("SCROLL_INSTRUCTIONS_FILE env var not set")
        data = json.loads(Path(instructions_path).read_text(encoding="utf-8"))

        blocks = data["blocks"]
        canvas_height = float(data["total_height"])

        # Total viewport spans top_pad + canvas_content + bottom_pad. The
        # camera frame is sized to this so the single rendered frame
        # contains everything plus enough margin for the runtime camera to
        # reach into.
        total_viewport_height = (
            CANVAS_TOP_PADDING + canvas_height + CANVAS_BOTTOM_PADDING
        )

        self.camera.frame.set(height=total_viewport_height)
        # Position the frame so the visible y-range is
        # [-(canvas_height + bottom_pad), top_pad]:
        frame_center_y = (
            CANVAS_TOP_PADDING - canvas_height - CANVAS_BOTTOM_PADDING
        ) / 2
        self.camera.frame.move_to([0, frame_center_y, 0])

        # Add every block at its laid-out y position (same code path as
        # ScrollScene's construct, minus the camera animation).
        for layout in blocks:
            mob = self._mobject_for(layout)
            if mob is None:
                continue
            x_target = self._x_for(layout)
            if layout["kind"] == "bullet":
                mob.move_to([x_target + mob.width / 2, layout["y_center"], 0])
            else:
                mob.move_to([x_target, layout["y_center"], 0])
            self.add(mob)

        # Hold for one frame so manim's "save last frame" emits the PNG.
        self.wait(1.0 / 60.0)
