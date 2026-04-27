from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from manim import BOLD, Circle, ITALIC, Mobject, Rectangle, Square, Text, VGroup, WHITE

from .object_base import ObjectBase


@dataclass
class ShapeObject(ObjectBase):
    border_color: object = WHITE
    fill_color: object | None = None
    text: str | None = None
    text_color: object = WHITE

    def set_border(self, color: object = WHITE) -> ShapeObject:
        self.border_color = color
        return self

    def set_fill(self, color: object | None = None) -> ShapeObject:
        self.fill_color = color
        return self

    def set_text(self, text: str | None = None, text_color: object = WHITE) -> ShapeObject:
        self.text = text
        self.text_color = text_color
        return self

    def set_size(self, _size: float) -> ShapeObject:
        raise NotImplementedError

    def _apply_text(self, shape_mobject: Mobject) -> Mobject:
        if self.text is None:
            return shape_mobject

        label = Text(self.text, color=self.text_color, font="Arial")
        label.scale_to_fit_width(max(shape_mobject.width * 0.8, 0.2))
        label.move_to(shape_mobject.get_center())
        return VGroup(shape_mobject, label)

    def draw(self) -> Mobject:
        raise NotImplementedError

    @classmethod
    def build(cls, config: dict[str, Any]) -> ShapeObject:
        instance = cls()
        position = cls._point2d(config.get("position"), default=(0.0, 0.0))
        size = cls._number(config.get("size"), default=1.6)
        instance.set_position(position[0], position[1])
        instance.set_size(size)
        instance.set_border(config.get("border_color", WHITE))
        instance.set_fill(config.get("fill_color"))
        instance.set_text(config.get("text"), config.get("text_color", WHITE))
        return instance


@dataclass
class CircleShape(ShapeObject):
    radius: float = 0.8

    def set_size(self, size: float) -> CircleShape:
        self.radius = size
        return self

    def draw(self) -> Mobject:
        circle = Circle(radius=self.radius, stroke_color=self.border_color)
        if self.fill_color is None:
            circle.set_fill(opacity=0)
        else:
            circle.set_fill(self.fill_color, opacity=1)
        return self._move_to_position(self._apply_text(circle))


@dataclass
class SquareShape(ShapeObject):
    length: float = 1.6

    def set_size(self, size: float) -> SquareShape:
        self.length = size
        return self

    def draw(self) -> Mobject:
        square = Square(side_length=self.length, stroke_color=self.border_color)
        if self.fill_color is None:
            square.set_fill(opacity=0)
        else:
            square.set_fill(self.fill_color, opacity=1)
        return self._move_to_position(self._apply_text(square))


@dataclass
class RectangleShape(ShapeObject):
    breadth: float = 1.0

    def set_size(self, size: float) -> RectangleShape:
        self.breadth = size
        return self

    def draw(self) -> Mobject:
        rectangle = Rectangle(width=self.breadth, height=self.breadth * 2, stroke_color=self.border_color)
        if self.fill_color is None:
            rectangle.set_fill(opacity=0)
        else:
            rectangle.set_fill(self.fill_color, opacity=1)
        return self._move_to_position(self._apply_text(rectangle))


@dataclass
class HeadingShape(ShapeObject):
    """Bold white text, no background. Size = target width in manim units."""
    target_width: float = 10.0  # ~70% of the 14.22 frame width

    def set_size(self, size: float) -> HeadingShape:
        self.target_width = size
        return self

    def draw(self) -> Mobject:
        text = Text(self.text or "", color=self.text_color, weight=BOLD, font="Arial")
        if text.width > 0:
            text.scale_to_fit_width(self.target_width)
        return self._move_to_position(text)


@dataclass
class QuoteShape(ShapeObject):
    """Italic white text wrapped in quotes, no background."""
    target_width: float = 10.0

    def set_size(self, size: float) -> QuoteShape:
        self.target_width = size
        return self

    def draw(self) -> Mobject:
        quoted = f'"{self.text}"' if self.text else ""
        text = Text(quoted, color=self.text_color, slant=ITALIC, font="Arial")
        if text.width > 0:
            text.scale_to_fit_width(self.target_width)
        return self._move_to_position(text)


@dataclass
class ListItemShape(ShapeObject):
    """Bullet item — left-anchored bold white text with a leading bullet.

    Position represents the LEFT edge of the text, not the center, so items
    in a vertical list line up cleanly regardless of length.
    """
    target_width: float = 11.0  # max width (clamped)
    text_height: float = 0.5

    def set_size(self, size: float) -> ListItemShape:
        self.target_width = size
        return self

    def draw(self) -> Mobject:
        body = self.text or ""
        full = f"•  {body}" if body else "•"
        text = Text(full, color=self.text_color, font="Arial", weight=BOLD)
        text.scale_to_fit_height(self.text_height)
        # Clamp horizontal width if too long
        if text.width > self.target_width:
            text.scale_to_fit_width(self.target_width)
        # Position represents the LEFT edge — shift so left edge sits at position.x
        target_x, target_y = self.position
        text.move_to([target_x + text.width / 2, target_y, 0])
        return text


@dataclass
class AutoRectangleShape(ShapeObject):
    """Rectangle that auto-sizes its width to fit the text inside, with padding."""
    min_width: float = 1.5
    text_height: float = 0.5
    padding_x: float = 0.5
    padding_y: float = 0.3

    def set_size(self, size: float) -> AutoRectangleShape:
        self.min_width = size
        return self

    def draw(self) -> Mobject:
        if self.text:
            label = Text(self.text, color=self.text_color, font="Arial")
            label.scale_to_fit_height(self.text_height)
        else:
            label = None

        if label is not None:
            width = max(label.width + self.padding_x * 2, self.min_width)
            height = label.height + self.padding_y * 2
        else:
            width = self.min_width
            height = self.text_height + self.padding_y * 2

        rectangle = Rectangle(width=width, height=height, stroke_color=self.border_color)
        if self.fill_color is None:
            rectangle.set_fill(opacity=0)
        else:
            rectangle.set_fill(self.fill_color, opacity=1)

        if label is not None:
            label.move_to(rectangle.get_center())
            result = VGroup(rectangle, label)
        else:
            result = rectangle

        return self._move_to_position(result)
