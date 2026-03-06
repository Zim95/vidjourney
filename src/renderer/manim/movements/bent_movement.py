from __future__ import annotations

from dataclasses import dataclass

from .movements import MovementBase, Point2D


@dataclass
class BentMovement(MovementBase):
    bend_direction: str = "up"

    def break_down_path(self, path: list[Point2D]) -> list[Point2D]:
        return {
            True: lambda: path,
            False: lambda: self._from_two_points(path),
        }[len(path) > 2]()

    def _from_two_points(self, path: list[Point2D]) -> list[Point2D]:
        defaults = {
            True: lambda: [path[0], path[1]],
            False: lambda: [(0.0, 0.0), (1.0, 0.0)],
        }[len(path) >= 2]()

        start = defaults[0]
        end = defaults[-1]
        middle_x = (start[0] + end[0]) / 2.0
        delta_y = abs(end[1] - start[1])
        delta_x = abs(end[0] - start[0])
        offset = max(0.8, delta_y, delta_x / 2.0)
        sign = {"up": 1.0, "down": -1.0}.get(self.bend_direction, 1.0)

        return [
            start,
            (middle_x, start[1]),
            (middle_x, start[1] + sign * offset),
            end,
        ]
