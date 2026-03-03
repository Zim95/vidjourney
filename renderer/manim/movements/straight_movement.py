from __future__ import annotations

from dataclasses import dataclass

from .movements import MovementBase, Point2D


@dataclass
class StraightMovement(MovementBase):
    def break_down_path(self, path: list[Point2D]) -> list[Point2D]:
        has_two_or_more = len(path) >= 2
        resolver = {
            True: lambda: [path[0], path[-1]],
            False: lambda: [(0.0, 0.0), (1.0, 0.0)],
        }
        return resolver[has_two_or_more]()
