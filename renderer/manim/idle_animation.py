from __future__ import annotations

from manim import Animation, VGroup, VMobject, Wait


class IdleAnimation:
    def create(self, _element_mobject: VMobject | VGroup) -> Animation:
        raise NotImplementedError


class Idle(IdleAnimation):
    def __init__(self, duration: float = 0.5):
        self.duration = duration

    def create(self, _element_mobject: VMobject | VGroup) -> Animation:
        return Wait(self.duration)
