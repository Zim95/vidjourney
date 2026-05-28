from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from manim import Animation, Scene


@dataclass
class AnimationBase:
    duration: float = 0.5
    object: object | None = None
    repeat: bool = False

    def bind(self, mobject: object) -> AnimationBase:
        self.object = mobject
        return self

    def animate(self, scene: Scene) -> None:
        animation = self.as_animation()
        animation is not None and scene.play(animation)

    def as_animation(self) -> Animation | None:
        raise NotImplementedError

    @classmethod
    def build(cls, config: Any = None, default_duration: float = 0.5) -> AnimationBase:
        duration = default_duration
        if isinstance(config, dict):
            duration = cls._duration_from_dict(config, default_duration)
        elif isinstance(config, (int, float)):
            duration = float(config)
        return cls(duration=duration)

    @staticmethod
    def _duration_from_dict(config: dict[str, Any], default_duration: float) -> float:
        try:
            if "duration" in config:
                return float(config["duration"])
            if "run_time" in config:
                return float(config["run_time"])
            return default_duration
        except (TypeError, ValueError):
            return default_duration
