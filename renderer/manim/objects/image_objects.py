from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from manim import ImageMobject, Mobject, SVGMobject

from .object_base import ObjectBase


@dataclass
class ImageObject(ObjectBase):
    url: str | Path | None = None
    size: float = 1.5

    def set_url(self, url: str | Path | None) -> ImageObject:
        self.url = url
        return self

    def set_size(self, size: float) -> ImageObject:
        self.size = size
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
        return self._move_to_position(mobject)
