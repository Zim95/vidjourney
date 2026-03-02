from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Type

from manim import Mobject, Scene

from .animations.arrow_animations import ArrowAnimation
from .animations.image_animations import ImageAnimation
from .animations.shape_animations import ShapeAnimation, ShapePopOutAnimation, ShapePopUpAnimation
from .manim_constants import (
    ARROW_OBJECT_MAP,
    ARROW_PHASE_ANIMATION_MAP,
    DEFAULT_ARROW_OBJECT,
    DEFAULT_SHAPE_OBJECT,
    IMAGE_PHASE_ANIMATION_MAP,
    SHAPE_OBJECT_MAP,
    SHAPE_PHASE_ANIMATION_MAP,
)
from .objects.arrow_objects import (
    ArrowObject,
)
from .objects.image_objects import ImageObject
from .objects.shape_objects import ShapeObject, SquareShape


@dataclass
class Elements:
    name: str
    type: str = "shape"
    shape: ShapeObject = field(default_factory=SquareShape)
    image: ImageObject | None = None
    spawn_animation: ShapeAnimation | ArrowAnimation | ImageAnimation = field(default_factory=ShapePopUpAnimation)
    idle_animation: ShapeAnimation | ArrowAnimation | ImageAnimation | None = None
    movement: object | None = None
    remove_animation: ShapeAnimation | ArrowAnimation | ImageAnimation = field(default_factory=ShapePopOutAnimation)
    _mobject: Mobject | None = field(default=None, init=False, repr=False)

    def set_type(self, element_type: str) -> Elements:
        self.type = element_type
        return self

    def set_shape(self, shape: ShapeObject | ArrowObject) -> Elements:
        self.shape = shape
        return self

    def set_image(self, image_url: str | Path, size: float | None = None) -> Elements:
        image = ImageObject().set_url(image_url)
        size is not None and image.set_size(size)
        self.image = image
        return self

    def set_spawn_animation(self, animation: ShapeAnimation | ArrowAnimation | ImageAnimation) -> Elements:
        self.spawn_animation = animation
        return self

    def set_idle_animation(self, animation: ShapeAnimation | ArrowAnimation | ImageAnimation) -> Elements:
        self.idle_animation = animation
        return self

    def set_movement(self, movement: object) -> Elements:
        self.movement = movement
        return self

    def set_remove_animation(self, animation: ShapeAnimation | ArrowAnimation | ImageAnimation) -> Elements:
        self.remove_animation = animation
        return self

    def _resolve_drawable(self) -> ShapeObject | ArrowObject | ImageObject:
        resolvers: dict[str, Callable[[], ShapeObject | ArrowObject | ImageObject | None]] = {
            "image": lambda: self.image,
            "arrow": lambda: {True: self.shape, False: None}[isinstance(self.shape, ArrowObject)],
            "shape": lambda: {True: self.shape, False: None}[isinstance(self.shape, ShapeObject)],
        }
        resolved = resolvers.get(self.type, lambda: None)()
        fallback = {True: self.shape, False: SquareShape()}[isinstance(self.shape, (ShapeObject, ArrowObject))]
        return resolved or fallback

    def _spawn_once(self, scene: Scene) -> None:
        drawable = self._resolve_drawable()
        self._mobject = drawable.draw()
        self.spawn_animation.bind(self._mobject).animate(scene)

    def _run_idle(self, scene: Scene) -> None:
        self._mobject is not None and self.idle_animation is not None and self.idle_animation.bind(self._mobject).animate(scene)

    def spawn(self, scene: Scene) -> Mobject:
        self._mobject is None and self._spawn_once(scene)
        self._run_idle(scene)

        return self._mobject

    def idle(self, scene: Scene) -> None:
        self._run_idle(scene)

    def move(self, scene: Scene) -> None:
        self._mobject is not None and callable(self.movement) and self.movement(scene, self._mobject)

    def close(self, scene: Scene) -> None:
        self._mobject is not None and self.remove_animation.bind(self._mobject).animate(scene)
        self._mobject is not None and setattr(self, "_mobject", None)


class ElementBuilder:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def build(self) -> Elements:
        name = str(self.config.get("name", "ELEMENT"))
        element_type = str(self.config.get("type", "shape")).lower()
        element = Elements(name=name).set_type(element_type)

        builders: dict[str, Callable[[], None]] = {
            "shape": lambda: element.set_shape(self._build_shape()),
            "arrow": lambda: element.set_shape(self._build_arrow()),
            "image": lambda: self._assign_image(element),
        }
        builders.get(element_type, lambda: None)()

        spawn_animation = self._build_animation(self.config.get("spawn_animation"), "spawn", element_type)
        self._apply_animation(element.set_spawn_animation, spawn_animation)

        idle_animation = self._build_animation(self.config.get("idle_animation"), "idle", element_type)
        self._apply_animation(element.set_idle_animation, idle_animation)

        remove_animation = self._build_animation(self.config.get("remove_animation"), "remove", element_type)
        self._apply_animation(element.set_remove_animation, remove_animation)

        return element

    def _assign_image(self, element: Elements) -> None:
        image_path = self.config.get("image") or self.config.get("url")
        size = self._number(self.config.get("size"), 1.5)
        image_path is not None and element.set_image(str(image_path), size=size)

    @staticmethod
    def _apply_animation(setter: Callable[[ShapeAnimation | ArrowAnimation | ImageAnimation], Elements], animation: ShapeAnimation | ArrowAnimation | ImageAnimation | None) -> None:
        animation is not None and setter(animation)

    def _build_shape(self) -> ShapeObject:
        shape_name = str(self.config.get("shape", "square")).lower()
        shape = SHAPE_OBJECT_MAP.get(shape_name, DEFAULT_SHAPE_OBJECT)()

        position = self._point2d(self.config.get("position"), default=(0.0, 0.0))
        shape.set_position(position[0], position[1])

        size = self._number(self.config.get("size"), 1.6)
        shape.set_size(size)
        shape.set_border(self.config.get("border_color", "WHITE"))
        shape.set_fill(self.config.get("fill_color"))
        shape.set_text(self.config.get("text"), self.config.get("text_color", "WHITE"))
        return shape

    def _build_arrow(self) -> ArrowObject:
        arrow_name = str(self.config.get("shape", self.config.get("arrow_type", "unidirectional_dotted"))).lower()
        arrow = ARROW_OBJECT_MAP.get(arrow_name, DEFAULT_ARROW_OBJECT)()

        from_position = self._point3d(self.config.get("from"), default=(0.0, 0.0, 0.0))
        to_position = self._point3d(self.config.get("to"), default=(1.0, 0.0, 0.0))
        path = [self._point3d(point, default=(0.0, 0.0, 0.0)) for point in self.config.get("path", [])]

        arrow.set_border(self.config.get("border_color", "WHITE"))
        arrow.set_direction(from_position, to_position)
        arrow.set_path(path)
        return arrow

    def _build_animation(self, config_value: Any, phase: str, element_type: str):
        builders: dict[bool, Callable[[], ShapeAnimation | ArrowAnimation | ImageAnimation | None]] = {
            True: lambda: None,
            False: lambda: self._build_animation_from_value(config_value, phase, element_type),
        }
        return builders[config_value is None]()

    def _build_animation_from_value(self, config_value: Any, phase: str, element_type: str):
        parser_map: dict[bool, Callable[[], tuple[str, float]]] = {
            True: lambda: (
                str(config_value.get("type", "")).lower(),
                self._number(config_value.get("duration"), self._number(config_value.get("run_time"), 0.45)),
            ),
            False: lambda: (str(config_value).lower(), self._number(self.config.get(f"{phase}_duration"), 0.45)),
        }
        animation_name, duration = parser_map[isinstance(config_value, dict)]()

        shape_cls = SHAPE_PHASE_ANIMATION_MAP.get(phase)
        image_cls = IMAGE_PHASE_ANIMATION_MAP.get(phase)
        arrow_cls = ARROW_PHASE_ANIMATION_MAP.get(phase, {}).get(animation_name)

        element_handlers: dict[str, Callable[[], ShapeAnimation | ArrowAnimation | ImageAnimation | None]] = {
            "shape": lambda: self._instantiate_optional(shape_cls, duration),
            "image": lambda: self._instantiate_optional(image_cls, duration),
            "arrow": lambda: self._instantiate_optional(arrow_cls, duration),
        }
        return element_handlers.get(element_type, lambda: None)()

    @staticmethod
    def _instantiate_optional(animation_cls: Type[ShapeAnimation | ArrowAnimation | ImageAnimation] | None, duration: float):
        constructors: dict[bool, Callable[[], ShapeAnimation | ArrowAnimation | ImageAnimation | None]] = {
            True: lambda: None,
            False: lambda: animation_cls(duration=duration),
        }
        return constructors[animation_cls is None]()

    @staticmethod
    def _number(value: Any, default: float) -> float:
        try:
            return {True: lambda: default, False: lambda: float(value)}[value is None]()
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _point2d(value: Any, default: tuple[float, float]) -> tuple[float, float]:
        sequence = {True: value, False: ()}[isinstance(value, (list, tuple))]
        try:
            extractors: dict[bool, Callable[[], tuple[float, float]]] = {
                True: lambda: default,
                False: lambda: (float(sequence[0]), float(sequence[1])),
            }
            return extractors[len(sequence) < 2]()
        except (TypeError, ValueError, IndexError):
            return default

    @staticmethod
    def _point3d(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
        sequence = {True: list(value), False: []}[isinstance(value, (list, tuple))]
        try:
            extractors: dict[bool, Callable[[], tuple[float, float, float]]] = {
                True: lambda: default,
                False: lambda: (
                    float((sequence + [0.0, 0.0, 0.0])[0]),
                    float((sequence + [0.0, 0.0, 0.0])[1]),
                    float((sequence + [0.0, 0.0, 0.0])[2]),
                ),
            }
            return extractors[len(sequence) < 2]()
        except (TypeError, ValueError, IndexError):
            return default
