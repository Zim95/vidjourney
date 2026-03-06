from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from manim import Animation, MoveAlongPath, Mobject, Scene, Succession, VMobject


Point2D = tuple[float, float]
Point3D = tuple[float, float, float]


@dataclass
class MovementBase:
    duration: float = 1.0
    repeat: int = 1
    reverse: bool = False
    object: Mobject | None = None
    path: list[Point2D] = field(default_factory=list)

    def bind(self, mobject: Mobject) -> MovementBase:
        self.object = mobject
        return self

    @classmethod
    def build(cls, config: dict[str, Any], **kwargs: Any) -> MovementBase:
        raw_path = config.get("path", [])
        parsed_path = [cls._point2d(point) for point in raw_path]
        instance = cls(**kwargs)
        instance.duration = cls._number(config.get("duration"), 1.0)
        instance.repeat = cls._integer(config.get("repeat"), 1)
        instance.reverse = cls._to_bool(config.get("reverse"), False)
        instance.path = parsed_path
        return instance

    def break_down_path(self, path: list[Point2D]) -> list[Point2D]:
        return path

    def animate(self, scene: Scene) -> None:
        animation = self.as_animation()
        animation is not None and scene.play(animation)

    def as_animation(self) -> Animation | None:
        valid = self.object is not None and len(self.path) >= 2
        if not valid:
            return None

        base_path = self.break_down_path(self.path)
        clips = [
            MoveAlongPath(self.object, self._to_path_mobject(current_path), run_time=self.duration)
            for current_path in self._expanded_paths(base_path)
        ]
        return Succession(*clips) if clips else None

    def total_duration(self) -> float:
        valid = self.object is not None and len(self.path) >= 2
        if not valid:
            return 0.0
        return float(len(self._expanded_paths(self.break_down_path(self.path)))) * float(self.duration)

    def _animate_valid(self, scene: Scene) -> None:
        base_path = self.break_down_path(self.path)
        for current_path in self._expanded_paths(base_path):
            scene.play(MoveAlongPath(self.object, self._to_path_mobject(current_path)), run_time=self.duration)

    def _expanded_paths(self, path: list[Point2D]) -> list[list[Point2D]]:
        iterations = max(1, self.repeat)
        forward = list(path)
        backward = list(reversed(path))
        expanded: list[list[Point2D]] = []
        for _ in range(iterations):
            expanded.append(forward)
            self.reverse and expanded.append(backward)
        return expanded

    @staticmethod
    def _to_path_mobject(path: list[Point2D]) -> VMobject:
        points3d = [(point[0], point[1], 0.0) for point in path]
        line = VMobject()
        line.set_points_as_corners(points3d)
        return line

    @staticmethod
    def _point2d(value: Any) -> Point2D:
        valid = isinstance(value, (list, tuple)) and len(value) >= 2
        resolver = {
            True: lambda: (float(value[0]), float(value[1])),
            False: lambda: (0.0, 0.0),
        }
        try:
            return resolver[valid]()
        except (TypeError, ValueError):
            return (0.0, 0.0)

    @staticmethod
    def _number(value: Any, default: float) -> float:
        try:
            return {True: default, False: float(value)}[value is None]
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _integer(value: Any, default: int) -> int:
        try:
            return max(1, int({True: default, False: value}[value is None]))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool(value: Any, default: bool) -> bool:
        normalized = str(value).strip().lower()
        token_map = {
            "1": True,
            "true": True,
            "yes": True,
            "y": True,
            "on": True,
            "0": False,
            "false": False,
            "no": False,
            "n": False,
            "off": False,
        }
        return token_map.get(normalized, default)
