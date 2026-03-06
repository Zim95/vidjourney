from __future__ import annotations

from manim import Animation, FadeIn, FadeOut, Scene

from .animations import AnimationBase


class ImageAnimation(AnimationBase):
    pass


class ImagePopUpAnimation(ImageAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return FadeIn(self.object, run_time=self.duration)


class ImagePopOutAnimation(ImageAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return FadeOut(self.object, run_time=self.duration)
