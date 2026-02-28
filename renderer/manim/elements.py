from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from manim import (
    Animation,
    Arrow,
    Circle,
    ImageMobject,
    LEFT,
    Line,
    RIGHT,
    Scene,
    Succession,
    SVGMobject,
    Text,
    VGroup,
    VMobject,
    WHITE,
)

from .idle_animation import Idle, IdleAnimation
from .movement import Movement, NoMovement, Point3D
from .remove_animation import PopOut, RemoveAnimation
from .spawn_animation import PopIn, SpawnAnimation


@dataclass
class Element:
    name: str
    size: float
    alt_text: str | None = None
    alt_color: object | None = None
    image: str | Path | None = None
    alt_border_color: object = WHITE
    spawn_animation: SpawnAnimation = field(default_factory=PopIn)
    idle_animation: IdleAnimation = field(default_factory=Idle)
    movement: Movement = field(default_factory=NoMovement)
    remove_animation: RemoveAnimation = field(default_factory=PopOut)

    def set_spawn_animation(self, spawn_animation: SpawnAnimation) -> Element:
        self.spawn_animation = spawn_animation
        return self

    def set_idle_animation(self, idle_animation: IdleAnimation) -> Element:
        self.idle_animation = idle_animation
        return self

    def set_movement(self, movement: Movement) -> Element:
        self.movement = movement
        return self

    def set_remove_animation(self, remove_animation: RemoveAnimation) -> Element:
        self.remove_animation = remove_animation
        return self

    def set_alt_text(self, alt_text: str | None) -> Element:
        self.alt_text = alt_text
        return self

    def set_alt_color(self, alt_color: object | None) -> Element:
        self.alt_color = alt_color
        return self

    def set_alt_border_color(self, alt_border_color: object) -> Element:
        self.alt_border_color = alt_border_color
        return self

    def set_image(self, image: str | Path | None) -> Element:
        self.image = image
        return self

    def build_mobject(self) -> VMobject | VGroup:
        image_mobject = self._load_image_mobject()
        if image_mobject is not None:
            image_mobject.stretch_to_fit_width(self.size)
            image_mobject.stretch_to_fit_height(self.size)
            return image_mobject

        radius = self.size / 2
        circle = Circle(radius=radius, stroke_color=self.alt_border_color)
        if self.alt_color is None:
            circle.set_fill(opacity=0)
        else:
            circle.set_fill(self.alt_color, opacity=1)

        if self.alt_text is None:
            return circle

        label = Text(self.alt_text)
        max_text_width = max(self.size * 0.8, 0.2)
        if label.width > max_text_width:
            label.scale_to_fit_width(max_text_width)
        label.move_to(circle.get_center())
        return VGroup(circle, label)

    def create_spawn(self, element_mobject: VMobject | VGroup) -> Animation:
        return self.spawn_animation.create(element_mobject)

    def create_idle(self, element_mobject: VMobject | VGroup) -> Animation:
        return self.idle_animation.create(element_mobject)

    def create_movement(self, element_mobject: VMobject | VGroup) -> Animation:
        return self.movement.create(element_mobject)

    def create_remove(self, element_mobject: VMobject | VGroup) -> Animation:
        return self.remove_animation.create(element_mobject)

    def play_full_cycle(self, scene: Scene) -> VMobject | VGroup:
        mobject = self.build_mobject()
        scene.play(self.create_spawn(mobject))
        scene.play(self.create_idle(mobject))
        scene.play(self.create_movement(mobject))
        scene.play(self.create_remove(mobject))
        return mobject

    def _load_image_mobject(self) -> VMobject | None:
        if self.image is None:
            return None

        image_path = Path(self.image)
        if not image_path.exists() or not image_path.is_file():
            return None

        if image_path.suffix.lower() == ".svg":
            return SVGMobject(str(image_path))

        return ImageMobject(str(image_path))


@dataclass
class ArrowElement(Element):
    name: str = "ARROW"
    size: float = 1.0
    start_position: Point3D = field(default_factory=lambda: LEFT)
    end_position: Point3D = field(default_factory=lambda: RIGHT)

    def to_and_from(self, start_position: Point3D, end_position: Point3D) -> ArrowElement:
        self.start_position = start_position
        self.end_position = end_position
        return self

    def from_(self, start_position: Point3D) -> ArrowElement:
        self.start_position = start_position
        return self

    def to(self, end_position: Point3D) -> ArrowElement:
        self.end_position = end_position
        return self

    def extract_path(self) -> VMobject:
        return Line(self.start_position, self.end_position)

    def build_mobject(self) -> Arrow:
        return Arrow(start=self.start_position, end=self.end_position)


class ElementBuilder:
    def __init__(self, name: str, size: float):
        self._element = Element(name=name, size=size)

    def set_spawn_animation(self, spawn_animation: SpawnAnimation) -> ElementBuilder:
        self._element.set_spawn_animation(spawn_animation)
        return self

    def set_idle_animation(self, idle_animation: IdleAnimation) -> ElementBuilder:
        self._element.set_idle_animation(idle_animation)
        return self

    def set_movement(self, movement: Movement) -> ElementBuilder:
        self._element.set_movement(movement)
        return self

    def set_remove_animation(self, remove_animation: RemoveAnimation) -> ElementBuilder:
        self._element.set_remove_animation(remove_animation)
        return self

    def set_alt_text(self, alt_text: str | None) -> ElementBuilder:
        self._element.set_alt_text(alt_text)
        return self

    def set_alt_color(self, alt_color: object | None) -> ElementBuilder:
        self._element.set_alt_color(alt_color)
        return self

    def set_image(self, image: str | Path | None) -> ElementBuilder:
        self._element.set_image(image)
        return self

    def set_alt_border_color(self, alt_border_color: object) -> ElementBuilder:
        self._element.set_alt_border_color(alt_border_color)
        return self

    def build(self) -> Element:
        return self._element


class ArrowElementBuilder:
    def __init__(self):
        self._arrow = ArrowElement()

    def to_and_from(self, start_position: Point3D, end_position: Point3D) -> ArrowElementBuilder:
        self._arrow.to_and_from(start_position, end_position)
        return self

    def from_(self, start_position: Point3D) -> ArrowElementBuilder:
        self._arrow.from_(start_position)
        return self

    def to(self, end_position: Point3D) -> ArrowElementBuilder:
        self._arrow.to(end_position)
        return self

    def set_spawn_animation(self, spawn_animation: SpawnAnimation) -> ArrowElementBuilder:
        self._arrow.set_spawn_animation(spawn_animation)
        return self

    def set_idle_animation(self, idle_animation: IdleAnimation) -> ArrowElementBuilder:
        self._arrow.set_idle_animation(idle_animation)
        return self

    def set_movement(self, movement: Movement) -> ArrowElementBuilder:
        self._arrow.set_movement(movement)
        return self

    def set_remove_animation(self, remove_animation: RemoveAnimation) -> ArrowElementBuilder:
        self._arrow.set_remove_animation(remove_animation)
        return self

    def build(self) -> ArrowElement:
        return self._arrow


def play_elements(scene: Scene, *elements: Element) -> None:
    for element in elements:
        element.play_full_cycle(scene)


def chain_animation(scene: Scene, *animations: Animation) -> None:
    scene.play(Succession(*animations))
