from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from manim import Mobject


@dataclass
class ObjectBase:
    position: tuple[float, float] = (0.0, 0.0)

    def set_position(self, x: float, y: float) -> ObjectBase:
        self.position = (x, y)
        return self

    def _move_to_position(self, mobject: Mobject) -> Mobject:
        mobject.move_to((self.position[0], self.position[1], 0.0))
        return mobject

    @staticmethod
    def _number(value: Any, default: float) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _point2d(value: Any, default: tuple[float, float]) -> tuple[float, float]:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return default
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _point3d(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return default
        try:
            z_value = value[2] if len(value) > 2 else 0.0
            return (float(value[0]), float(value[1]), float(z_value))
        except (TypeError, ValueError):
            return default
