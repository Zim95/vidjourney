from __future__ import annotations

from typing import Sequence

from manim import Animation, CubicBezier, Line, MoveAlongPath, VGroup, VMobject, Wait


Point3D = Sequence[float]


class Movement:
    def __init__(self, start_position: Point3D, end_position: Point3D, run_time: float = 1.2):
        self.start_position = start_position
        self.end_position = end_position
        self.run_time = run_time

    def path(self) -> VMobject:
        raise NotImplementedError

    def create(self, element_mobject: VMobject | VGroup) -> Animation:
        return MoveAlongPath(element_mobject, self.path(), run_time=self.run_time)


class NoMovement(Movement):
    def __init__(self, duration: float = 0.0):
        safe_duration = max(duration, 1e-6)
        super().__init__((0, 0, 0), (0, 0, 0), run_time=safe_duration)

    def path(self) -> VMobject:
        return Line((0, 0, 0), (0, 0, 0))

    def create(self, _element_mobject: VMobject | VGroup) -> Animation:
        return Wait(max(self.run_time, 1e-6))


class StraightLine(Movement):
    def path(self) -> VMobject:
        return Line(self.start_position, self.end_position)


class UpwardsCurve(Movement):
    def __init__(self, start_position: Point3D, end_position: Point3D, lift: float = 1.5, run_time: float = 1.4):
        super().__init__(start_position, end_position, run_time=run_time)
        self.lift = lift

    def path(self) -> VMobject:
        start = self.start_position
        end = self.end_position
        delta = [(end[i] - start[i]) for i in range(3)]
        control_1 = [start[0] + delta[0] * 0.25, start[1] + self.lift, start[2]]
        control_2 = [start[0] + delta[0] * 0.75, end[1] + self.lift, end[2]]
        return CubicBezier(start, control_1, control_2, end)


class DownwardsCurve(Movement):
    def __init__(self, start_position: Point3D, end_position: Point3D, drop: float = 1.5, run_time: float = 1.4):
        super().__init__(start_position, end_position, run_time=run_time)
        self.drop = drop

    def path(self) -> VMobject:
        start = self.start_position
        end = self.end_position
        delta = [(end[i] - start[i]) for i in range(3)]
        control_1 = [start[0] + delta[0] * 0.25, start[1] - self.drop, start[2]]
        control_2 = [start[0] + delta[0] * 0.75, end[1] - self.drop, end[2]]
        return CubicBezier(start, control_1, control_2, end)


class UpwardsBent(Movement):
    def __init__(self, start_position: Point3D, end_position: Point3D, bend: float = 1.0, run_time: float = 1.3):
        super().__init__(start_position, end_position, run_time=run_time)
        self.bend = bend

    def path(self) -> VMobject:
        start = self.start_position
        end = self.end_position
        mid_x = (start[0] + end[0]) / 2
        points = [
            start,
            [mid_x, start[1] + self.bend, start[2]],
            [mid_x, end[1] + self.bend, end[2]],
            end,
        ]
        path = VMobject()
        path.set_points_as_corners(points)
        return path


class DownwardsBent(Movement):
    def __init__(self, start_position: Point3D, end_position: Point3D, bend: float = 1.0, run_time: float = 1.3):
        super().__init__(start_position, end_position, run_time=run_time)
        self.bend = bend

    def path(self) -> VMobject:
        start = self.start_position
        end = self.end_position
        mid_x = (start[0] + end[0]) / 2
        points = [
            start,
            [mid_x, start[1] - self.bend, start[2]],
            [mid_x, end[1] - self.bend, end[2]],
            end,
        ]
        path = VMobject()
        path.set_points_as_corners(points)
        return path
