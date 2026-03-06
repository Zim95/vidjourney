from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manim import DOWN, ImageMobject, Mobject, SVGMobject, Text, VGroup, WHITE

from .object_base import ObjectBase


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

        mobject.stretch_to_fit_width(self.size)
        mobject.stretch_to_fit_height(self.size)
        image = self._move_to_position(mobject)

        if self.text is None:
            return image

        label = Text(self.text, color=self.text_color, font="Arial")
        label.scale_to_fit_width(max(image.width * 0.9, 0.2))
        label.next_to(image, DOWN, buff=0.12)
        group = VGroup(image, label)
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
        instance.set_text(config.get("text") or config.get("name"), config.get("text_color", WHITE))
        instance.set_position(position[0], position[1])
        return instance
