from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manim import DOWN, Group, ImageMobject, Mobject, SVGMobject, Text, VGroup, WHITE

from .object_base import ObjectBase


# Manim default camera frame: ~14.22 wide x 8.0 tall. Leave small margins.
FRAME_WIDTH = 14.22
FRAME_HEIGHT = 8.0
MARGIN = 0.3

# Caption block sizing (in manim units)
CAPTION_MAX_WIDTH_RATIO = 0.9   # caption width relative to image width
CAPTION_HEIGHT_RESERVE = 1.2    # vertical space reserved for caption + buffer
CAPTION_MAX_LINES_RATIO = 0.1   # caption max height (fraction of frame height)


@dataclass
class ImageObject(ObjectBase):
    url: str | Path | None = None
    size: float = 1.5
    text: str | None = None
    text_color: object = WHITE

    def set_url(self, url: str | Path | None) -> ImageObject:
        self.url = url
        return self

    def set_size(self, size: float) -> ImageObject:
        self.size = size
        return self

    def set_text(self, text: str | None = None, text_color: object = WHITE) -> ImageObject:
        self.text = text
        self.text_color = text_color
        return self

    def draw(self) -> Mobject:
        if self.url is None:
            raise ValueError("Image URL/path is not set")

        image_path = Path(self.url)
        if not image_path.exists() or not image_path.is_file():
            raise ValueError(f"Image path does not exist: {self.url}")

        if image_path.suffix.lower() == ".svg":
            mobject = SVGMobject(str(image_path))
        else:
            mobject = ImageMobject(str(image_path))

        # Fit the image within the available frame preserving aspect ratio.
        # If a caption is provided, leave room at the bottom.
        max_width = FRAME_WIDTH - 2 * MARGIN
        max_height = FRAME_HEIGHT - 2 * MARGIN
        if self.text:
            max_height -= CAPTION_HEIGHT_RESERVE

        scale = min(max_width / mobject.width, max_height / mobject.height)
        mobject.scale(scale)

        image = self._move_to_position(mobject)

        if self.text is None:
            return image

        label = Text(self.text, color=self.text_color, font="Arial")
        label.scale_to_fit_width(min(image.width * CAPTION_MAX_WIDTH_RATIO, max_width))
        # Clamp caption height so long captions don't overflow
        max_caption_height = FRAME_HEIGHT * CAPTION_MAX_LINES_RATIO
        if label.height > max_caption_height:
            label.scale_to_fit_height(max_caption_height)
        label.next_to(image, DOWN, buff=0.2)

        # Use Group (not VGroup) because ImageMobject is not a VMobject
        container_cls = Group if isinstance(image, ImageMobject) else VGroup
        group = container_cls(image, label)
        group.shift(image.get_center() - group.get_center())
        return group

    @classmethod
    def build(cls, config: dict[str, Any]) -> ImageObject | None:
        image_path = config.get("image") or config.get("url")
        if image_path is None:
            return None

        size_value = cls._number(config.get("size"), default=1.5)
        position = cls._point2d(config.get("position"), default=(0.0, 0.0))
        instance = cls().set_url(str(image_path)).set_size(size_value)
        # Only use explicit 'text' as label — never the element's identifier 'name'.
        instance.set_text(config.get("text"), config.get("text_color", WHITE))
        instance.set_position(position[0], position[1])
        return instance
