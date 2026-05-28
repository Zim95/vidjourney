from __future__ import annotations

from dataclasses import dataclass

from .movements import MovementBase, Point2D


@dataclass
class CurveMovement(MovementBase):
    curve_direction: str = "up"

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
        midpoint_x = (start[0] + end[0]) / 2.0
        midpoint_y = (start[1] + end[1]) / 2.0
        spread = max(1.0, abs(end[0] - start[0]), abs(end[1] - start[1]))
        sign = {"up": 1.0, "down": -1.0}.get(self.curve_direction, 1.0)
        control = (midpoint_x, midpoint_y + sign * spread / 2.0)

        segments = 12
        return [self._quadratic_point(start, control, end, index / segments) for index in range(segments + 1)]

    @staticmethod
    def _quadratic_point(start: Point2D, control: Point2D, end: Point2D, t: float) -> Point2D:
        one_minus_t = 1.0 - t
        x_value = (one_minus_t * one_minus_t * start[0]) + (2 * one_minus_t * t * control[0]) + (t * t * end[0])
        y_value = (one_minus_t * one_minus_t * start[1]) + (2 * one_minus_t * t * control[1]) + (t * t * end[1])
        return (x_value, y_value)
