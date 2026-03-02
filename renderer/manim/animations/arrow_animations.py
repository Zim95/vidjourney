from __future__ import annotations

from manim import Create, FadeOut, Scene, ShowPassingFlash

from .animations import AnimationBase


class ArrowAnimation(AnimationBase):
    pass


class DottedArrowUnidirectionalIdleAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(ShowPassingFlash(self.object.copy(), time_width=0.25), run_time=self.duration)


class DottedArrowBidirectionalIdleAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(ShowPassingFlash(self.object.copy(), time_width=0.3), run_time=self.duration)


class UnidirectionalDottedArrowSpawnAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(Create(self.object), run_time=self.duration)


class UnidirectionalDottedArrowRemoveAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(FadeOut(self.object), run_time=self.duration)


class UnidirectionalSolidArrowSpawnAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(Create(self.object), run_time=self.duration)


class UnidirectionalSolidArrowRemoveAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(FadeOut(self.object), run_time=self.duration)


class BidirectionalSolidArrowSpawnAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(Create(self.object), run_time=self.duration)


class BidirectionalDottedArrowSpawnAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(Create(self.object), run_time=self.duration)


class BidirectionalDottedArrowRemoveAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(FadeOut(self.object), run_time=self.duration)


class BidirectionalSolidArrowRemoveAnimation(ArrowAnimation):
    def animate(self, scene: Scene) -> None:
        if self.object is None:
            return
        scene.play(FadeOut(self.object), run_time=self.duration)
