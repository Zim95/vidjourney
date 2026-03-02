from __future__ import annotations

from dataclasses import dataclass

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
