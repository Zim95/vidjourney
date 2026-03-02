from __future__ import annotations

from manim import FadeIn, FadeOut, Scene

from .animations import AnimationBase


class ImageAnimation(AnimationBase):
    pass


class ImagePopUpAnimation(ImageAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(FadeIn(self.object), run_time=self.duration)


class ImagePopOutAnimation(ImageAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(FadeOut(self.object), run_time=self.duration)
