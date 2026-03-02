from __future__ import annotations

from manim import FadeIn, FadeOut, Scene

from .animations import AnimationBase


class ShapeAnimation(AnimationBase):
    pass


class ShapePopUpAnimation(ShapeAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(FadeIn(self.object), run_time=self.duration)


class ShapePopOutAnimation(ShapeAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(FadeOut(self.object), run_time=self.duration)
