from __future__ import annotations

from dataclasses import dataclass

from manim import Scene


@dataclass
class AnimationBase:
    duration: float = 0.5
    object: object | None = None
    repeat: bool = False

    def bind(self, mobject: object) -> AnimationBase:
        self.object = mobject
        return self

    def animate(self, scene: Scene) -> None:
        raise NotImplementedError
