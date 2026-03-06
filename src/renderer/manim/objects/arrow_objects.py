from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2
from typing import Any

from manim import (
    Arrow,
    DashedVMobject,
    DoubleArrow,
    Line,
    Mobject,
    Triangle,
    VMobject,
    VGroup,
    WHITE,
)

from .object_base import ObjectBase


Point3D = tuple[float, float, float]


@dataclass
class ArrowObject(ObjectBase):
    border_color: object = WHITE
    from_position: Point3D = (0.0, 0.0, 0.0)
    to_position: Point3D = (1.0, 0.0, 0.0)
    path_points: list[Point3D] = field(default_factory=list)

    def set_border(self, color: object = WHITE) -> ArrowObject:
        self.border_color = color
        return self

    def set_direction(self, from_position: Point3D, to_position: Point3D) -> ArrowObject:
        self.from_position = from_position
        self.to_position = to_position
        return self

    def set_path(self, points: list[Point3D]) -> ArrowObject:
        self.path_points = points
        return self

    def _build_polyline(self) -> VMobject:
        points = [self.from_position, *self.path_points, self.to_position]
        line = VMobject(stroke_color=self.border_color)
        line.set_points_as_corners(points)
        return line

    def _tip(self, start: Point3D, end: Point3D) -> Mobject:
        angle = atan2(end[1] - start[1], end[0] - start[0])
        tip = Triangle(color=self.border_color, fill_opacity=1).scale(0.08)
        tip.rotate(angle - 1.5708)
        tip.move_to(end)
        return tip

    def draw(self) -> Mobject:
        if self.path_points:
            line = self._build_polyline()
            tip = self._tip(self.path_points[-1], self.to_position)
            return VGroup(line, tip)
        return Arrow(start=self.from_position, end=self.to_position, color=self.border_color, buff=0)

    @classmethod
    def build(cls, config: dict[str, Any]) -> ArrowObject:
        instance = cls()
        from_position = cls._point3d(config.get("from"), default=(0.0, 0.0, 0.0))
        to_position = cls._point3d(config.get("to"), default=(1.0, 0.0, 0.0))
        raw_points = config.get("path", [])
        path_points = [cls._point3d(point, default=(0.0, 0.0, 0.0)) for point in raw_points]
        instance.set_border(config.get("border_color", WHITE))
        instance.set_direction(from_position, to_position)
        instance.set_path(path_points)
        return instance


@dataclass
class SolidArrow(ArrowObject):
    def draw(self) -> Mobject:
        if self.path_points:
            return super().draw()
        return Arrow(start=self.from_position, end=self.to_position, color=self.border_color, buff=0)


@dataclass
class DottedArrow(ArrowObject):
    def draw(self) -> Mobject:
        if self.path_points:
            line = DashedVMobject(self._build_polyline())
            tip = self._tip(self.path_points[-1], self.to_position)
            return VGroup(line, tip)
        return DashedVMobject(Arrow(start=self.from_position, end=self.to_position, color=self.border_color, buff=0))


@dataclass
class UnidirectionalSolidArrow(SolidArrow):
    def draw(self) -> Mobject:
        if self.path_points:
            return super().draw()
        return Arrow(start=self.from_position, end=self.to_position, color=self.border_color, buff=0)


@dataclass
class BidirectionalSolidArrow(SolidArrow):
    def draw(self) -> Mobject:
        if self.path_points:
            line = self._build_polyline()
            start_tip = self._tip(self.path_points[0] if self.path_points else self.to_position, self.from_position)
            end_tip = self._tip(self.path_points[-1] if self.path_points else self.from_position, self.to_position)
            return VGroup(line, start_tip, end_tip)
        return DoubleArrow(start=self.from_position, end=self.to_position, color=self.border_color, buff=0)


@dataclass
class UnidirectionalDottedArrow(DottedArrow):
    def draw(self) -> Mobject:
        return super().draw()


@dataclass
class BidirectionalDottedArrow(DottedArrow):
    def draw(self) -> Mobject:
        if self.path_points:
            line = DashedVMobject(self._build_polyline())
            start_tip = self._tip(self.path_points[0] if self.path_points else self.to_position, self.from_position)
            end_tip = self._tip(self.path_points[-1] if self.path_points else self.from_position, self.to_position)
            return VGroup(line, start_tip, end_tip)

        dashed_line = DashedVMobject(Line(self.from_position, self.to_position, color=self.border_color))
        start_tip = self._tip(self.to_position, self.from_position)
        end_tip = self._tip(self.from_position, self.to_position)
        return VGroup(dashed_line, start_tip, end_tip)
