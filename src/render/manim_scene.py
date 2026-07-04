"""
Manim scene class for the scroll-rendering prototype.

Reads a JSON instructions file (path in ``SCROLL_INSTRUCTIONS_FILE`` env)
and renders a single continuous-scroll mp4 for the whole section.

Architecture:
- All blocks are placed at their final y-positions upfront — they're never
  spawned mid-scene the way the existing per-scene pipeline does.
- ``MovingCameraScene.camera.frame`` animates between waypoints provided in
  the instructions file. Linear interpolation between waypoints; final
  waypoint holds at the section's bottom.

Bullet styling mirrors ``ListItemShape`` from the page-break pipeline:
non-bold, ~0.4 height per line, soft-wrap at ~70 chars. Per-level glyph
("•" / "◦" / "▸") embedded in the displayed text. Indent grows by 0.6
units per level.

Image blocks load via manim's ``ImageMobject`` (raster) — code blocks /
tables / images from the source PDF are all rasterized so they share one
loading path.

Note: this is a prototype. Tuning happens in ``src/render/build.py``
(``SCROLL_RATE_UNITS_PER_SEC``, ``CAMERA_LEAD``, ``INTER_BLOCK_PAD``,
``BLOCK_HEIGHT``) — re-running the build re-emits the instructions, manim
re-renders against the new layout.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from manim import (
    BOLD, FadeIn, ImageMobject, ITALIC, MovingCameraScene, Text, VGroup, WHITE,
)


# Bullet styling (mirrors ListItemShape from the page-break pipeline)
BULLET_GLYPH = ["•", "◦", "▸"]
BULLET_LINE_HEIGHT = 0.4
# Wrap chars chosen so a full single line at ``BULLET_LINE_HEIGHT`` height
# stays under ``BULLET_TARGET_WIDTH`` without triggering scale_to_fit_width
# (which would shrink the line and produce visible size variance between
# bullets). 70 chars at height=0.4 in Arial measures ~14u — exceeds the
# 11u target. 55 chars stays under.
BULLET_WRAP_CHARS = 55
BULLET_INDENT_PER_LEVEL = 0.6
BULLET_LEFT_X = -5.5
BULLET_TARGET_WIDTH = 11.0

# Heading styling
HEADING_HEIGHT = 0.9
HEADING_TARGET_WIDTH = 11.0

# Paragraph styling — narrated prose shown as wrapped body text. Looks like a
# blockquote without the bullet glyph. Slightly smaller than bullets so the
# bullets visually dominate as the focal content.
PARAGRAPH_LINE_HEIGHT = 0.38
PARAGRAPH_WRAP_CHARS = 75
PARAGRAPH_TARGET_WIDTH = 11.0

# Quote styling
QUOTE_TARGET_WIDTH = 10.0
QUOTE_TEXT_HEIGHT = 0.5
QUOTE_WRAP_CHARS = 60  # tighter than bullets so italic text stays readable
ATTRIB_HEIGHT = 0.35

# Image styling — close to full viewport height so images "cover the whole
# area" while the camera continuously scrolls across them (user's ask).
IMAGE_TARGET_HEIGHT = 6.0
# Max width for an image. Wide-aspect images (e.g. landscape code snippets)
# get clamped to this; their height shrinks proportionally. Without this,
# a 4:1 aspect image at height=6 would be 24 units wide — exceeding the
# 14.22-unit canvas frame and rendering off the edges.
IMAGE_TARGET_WIDTH = 12.0
CAPTION_HEIGHT = 0.35
CAPTION_TARGET_WIDTH = 10.0


def _soft_wrap(text: str, wrap_chars: int, continuation_indent: int = 0) -> str:
    """Same soft-wrap logic as ListItemShape so layout math (build.py) and
    rendering stay in sync.

    ``continuation_indent`` prepends that many spaces to every line after
    the first. For bullets we use this to align wrapped continuation lines
    under the first-line text (past the bullet glyph) instead of letting
    them run back to the original left margin.
    """
    if len(text) <= wrap_chars:
        return text
    words = text.split()
    if not words:
        return text
    lines: list[str] = []
    line: list[str] = []
    line_len = 0
    for w in words:
        if line and line_len + len(w) + 1 > wrap_chars:
            lines.append(" ".join(line))
            line = [w]
            line_len = len(w)
        else:
            line.append(w)
            line_len += len(w) + (1 if len(line) > 1 else 0)
    if line:
        lines.append(" ".join(line))
    if continuation_indent > 0 and len(lines) > 1:
        prefix = " " * continuation_indent
        lines = [lines[0]] + [prefix + ln for ln in lines[1:]]
    return "\n".join(lines)


class ScrollScene(MovingCameraScene):
    def construct(self) -> None:
        instructions_path = os.environ.get("SCROLL_INSTRUCTIONS_FILE")
        if not instructions_path:
            raise RuntimeError("SCROLL_INSTRUCTIONS_FILE env var not set")
        data = json.loads(Path(instructions_path).read_text(encoding="utf-8"))

        blocks = data["blocks"]
        total_duration = float(data["total_duration"])
        camera_path = data.get("camera_path", [])

        # --- Lay out every block at its y-position. All static mobjects added
        # to scene before any animation runs. ---
        for layout in blocks:
            mob = self._mobject_for(layout)
            if mob is None:
                continue
            x_target = self._x_for(layout)
            if layout["kind"] == "bullet":
                # Bullet x is LEFT EDGE; mob center sits at x_left + width/2.
                # manim's move_to positions by center.
                mob.move_to([x_target + mob.width / 2, layout["y_center"], 0])
            else:
                mob.move_to([x_target, layout["y_center"], 0])
            self.add(mob)

        # Position the camera at the first waypoint (top of section) before
        # the first animation. Avoids a startup zoom from the manim default.
        if camera_path:
            self.camera.frame.move_to([0, camera_path[0]["y"], 0])

        # --- Animate the camera through the waypoints. Each segment runs for
        # the time delta between waypoints; linear interpolation between
        # waypoints. ---
        prev_t = 0.0
        prev_y = camera_path[0]["y"] if camera_path else 0.0
        for wp in camera_path[1:]:
            t = float(wp["t"])
            y = float(wp["y"])
            run_time = max(t - prev_t, 0.05)
            if abs(y - prev_y) < 0.01:
                # Hold — camera doesn't move during this block's narration
                self.wait(run_time)
            else:
                self.play(
                    self.camera.frame.animate.move_to([0, y, 0]),
                    run_time=run_time,
                )
            prev_t = t
            prev_y = y

        # Pad to total_duration if waypoints didn't fully cover it (shouldn't
        # happen with the current path, but defensive).
        if prev_t < total_duration:
            self.wait(total_duration - prev_t)

    # --- Mobject factory ---

    def _mobject_for(self, layout: dict):
        kind = layout["kind"]
        if kind == "heading":
            return self._heading_mobject(layout)
        if kind == "bullet":
            return self._bullet_mobject(layout)
        if kind == "image":
            return self._image_mobject(layout)
        if kind == "quote":
            return self._quote_mobject(layout)
        if kind == "paragraph":
            return self._paragraph_mobject(layout)
        return None

    def _paragraph_mobject(self, layout: dict):
        text_value = layout.get("display") or layout.get("text") or ""
        if not text_value.strip():
            return None
        wrapped = _soft_wrap(text_value, PARAGRAPH_WRAP_CHARS)
        text = Text(wrapped, color=WHITE, font="Arial")
        text.scale_to_fit_height(PARAGRAPH_LINE_HEIGHT * (wrapped.count("\n") + 1))
        if text.width > PARAGRAPH_TARGET_WIDTH:
            text.scale_to_fit_width(PARAGRAPH_TARGET_WIDTH)
        return text

    def _x_for(self, layout: dict) -> float:
        """Horizontal position. Headings/images/quotes centered; bullets
        left-anchored with per-level indent."""
        if layout["kind"] == "bullet":
            level = int(layout.get("level", 0))
            indent = BULLET_INDENT_PER_LEVEL * level
            # Position represents the LEFT edge of the bullet text. Mobject
            # centering math adds width/2 to get the final placement.
            return BULLET_LEFT_X + indent
        return 0.0

    def _heading_mobject(self, layout: dict) -> Text:
        text = Text(
            layout["display"] or layout["text"],
            color=WHITE, font="Arial", weight=BOLD,
        )
        text.scale_to_fit_height(HEADING_HEIGHT)
        if text.width > HEADING_TARGET_WIDTH:
            text.scale_to_fit_width(HEADING_TARGET_WIDTH)
        return text

    def _bullet_mobject(self, layout: dict) -> Text:
        level = int(layout.get("level", 0))
        glyph = BULLET_GLYPH[min(level, len(BULLET_GLYPH) - 1)]
        # The "•  " prefix (3 chars) acts as the visual hang-indent — passing
        # continuation_indent=3 makes wrapped lines line up under the first
        # word of the bullet text rather than under the glyph itself.
        wrapped = _soft_wrap(
            f"{glyph}  {layout['display']}",
            BULLET_WRAP_CHARS,
            continuation_indent=3,
        )
        text = Text(wrapped, color=WHITE, font="Arial")
        text.scale_to_fit_height(BULLET_LINE_HEIGHT * (wrapped.count("\n") + 1))
        if text.width > BULLET_TARGET_WIDTH:
            text.scale_to_fit_width(BULLET_TARGET_WIDTH)
        # Caller (``construct``) positions us by left-edge using x_left + width/2.
        return text

    def _image_mobject(self, layout: dict):
        path = layout.get("resource_path")
        caption = layout.get("caption") or ""
        if not path:
            return None
        img_path = Path(path)
        if not img_path.exists():
            return None
        image = ImageMobject(str(img_path))
        image.scale_to_fit_height(IMAGE_TARGET_HEIGHT)
        # Wide-aspect images (4:1 landscape, etc.) would extend off the
        # canvas at height=6. Clamp to the max width and let height shrink
        # proportionally — matches the bullet/quote clamps elsewhere.
        if image.width > IMAGE_TARGET_WIDTH:
            image.scale_to_fit_width(IMAGE_TARGET_WIDTH)
        if not caption:
            return image
        # Stack caption below the image as a tight VGroup-like arrangement
        # (ImageMobject can't share VGroup with Text; emulate via shift).
        cap_text = Text(caption, color=WHITE, font="Arial")
        cap_text.scale_to_fit_height(CAPTION_HEIGHT)
        if cap_text.width > CAPTION_TARGET_WIDTH:
            cap_text.scale_to_fit_width(CAPTION_TARGET_WIDTH)
        cap_text.next_to(image, direction=[0, -1, 0], buff=0.2)
        # Return a Group containing both — manim Group handles mixed types.
        from manim import Group
        return Group(image, cap_text)

    def _quote_mobject(self, layout: dict):
        # Soft-wrap the quote text so a 50-word Alan-Kay-style quote doesn't
        # render as one very long line. Without wrapping, ``scale_to_fit_width``
        # would crush the line down to a sliver — the previous bug.
        wrapped = _soft_wrap(f'"{layout["display"]}"', QUOTE_WRAP_CHARS)
        n_lines = wrapped.count("\n") + 1
        quote_text = Text(wrapped, color=WHITE, font="Arial", slant=ITALIC)
        quote_text.scale_to_fit_height(QUOTE_TEXT_HEIGHT * n_lines)
        if quote_text.width > QUOTE_TARGET_WIDTH:
            quote_text.scale_to_fit_width(QUOTE_TARGET_WIDTH)
        if not layout.get("attribution"):
            return quote_text
        attrib = Text(
            f"— {layout['attribution']}", color=WHITE, font="Arial",
        )
        attrib.scale_to_fit_height(ATTRIB_HEIGHT)
        attrib.next_to(quote_text, direction=[0, -1, 0], buff=0.3)
        return VGroup(quote_text, attrib)
