from __future__ import annotations

from manim import Animation, Create, FadeOut, Scene, ShowPassingFlash

from .animations import AnimationBase


class ArrowAnimation(AnimationBase):
    pass


class DottedArrowUnidirectionalIdleAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return ShowPassingFlash(self.object.copy(), time_width=0.25, run_time=self.duration)


class DottedArrowBidirectionalIdleAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return ShowPassingFlash(self.object.copy(), time_width=0.3, run_time=self.duration)


class UnidirectionalDottedArrowSpawnAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return Create(self.object, run_time=self.duration)


class UnidirectionalDottedArrowRemoveAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return FadeOut(self.object, run_time=self.duration)


class UnidirectionalSolidArrowSpawnAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return Create(self.object, run_time=self.duration)


class UnidirectionalSolidArrowRemoveAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return FadeOut(self.object, run_time=self.duration)


class BidirectionalSolidArrowSpawnAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return Create(self.object, run_time=self.duration)


class BidirectionalDottedArrowSpawnAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return Create(self.object, run_time=self.duration)


class BidirectionalDottedArrowRemoveAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return FadeOut(self.object, run_time=self.duration)


class BidirectionalSolidArrowRemoveAnimation(ArrowAnimation):
    def as_animation(self) -> Animation | None:
        if self.object is None:
            return None
        return FadeOut(self.object, run_time=self.duration)
