from __future__ import annotations

from manim import Animation, FadeOut, VGroup, VMobject


class RemoveAnimation:
    def create(self, element_mobject: VMobject | VGroup) -> Animation:
        raise NotImplementedError


class PopOut(RemoveAnimation):
    def __init__(self, run_time: float = 0.5):
        self.run_time = run_time

    def create(self, element_mobject: VMobject | VGroup) -> Animation:
        return FadeOut(element_mobject, run_time=self.run_time)
