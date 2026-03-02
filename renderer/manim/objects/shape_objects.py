from __future__ import annotations

from dataclasses import dataclass

from manim import Circle, Mobject, Rectangle, Square, Text, VGroup, WHITE

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

        label = Text(self.text, color=self.text_color)
        label.scale_to_fit_width(max(shape_mobject.width * 0.8, 0.2))
        label.move_to(shape_mobject.get_center())
        return VGroup(shape_mobject, label)

    def draw(self) -> Mobject:
        raise NotImplementedError


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
