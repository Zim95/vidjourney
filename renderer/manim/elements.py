from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Type

from manim import Mobject, Scene

from .animations.arrow_animations import ArrowAnimation
from .animations.image_animations import ImageAnimation
from .animations.shape_animations import ShapeAnimation, ShapePopOutAnimation, ShapePopUpAnimation
from .manim_constants import (
    OBJECT_APPLIERS,
    OBJECT_BUILDERS,
    resolve_animation_class,
    resolve_movement,
)
from .movements import MovementBase
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
    movement: MovementBase | object | None = None
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

    def set_image_object(self, image: ImageObject) -> Elements:
        self.image = image
        return self

    def set_spawn_animation(self, animation: ShapeAnimation | ArrowAnimation | ImageAnimation) -> Elements:
        self.spawn_animation = animation
        return self

    def set_idle_animation(self, animation: ShapeAnimation | ArrowAnimation | ImageAnimation) -> Elements:
        self.idle_animation = animation
        return self

    def set_movement(self, movement: MovementBase | object) -> Elements:
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
        return self._mobject

    def idle(self, scene: Scene) -> None:
        self._run_idle(scene)

    def move(self, scene: Scene) -> None:
        self._mobject is not None and self.movement is not None and self._run_movement(scene)

    def _run_movement(self, scene: Scene) -> None:
        movement_handlers: dict[bool, Callable[[], None]] = {
            True: lambda: self.movement(scene, self._mobject),
            False: lambda: hasattr(self.movement, "bind") and hasattr(self.movement, "animate") and self.movement.bind(self._mobject).animate(scene),
        }
        movement_handlers[callable(self.movement)]()

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

        OBJECT_APPLIERS.get(element_type, lambda _element, _built_object: None)(element, self._build_object(element_type))

        spawn_animation = self._build_animation(self.config.get("spawn_animation"), "spawn", element_type)
        self._apply_animation(element.set_spawn_animation, spawn_animation)

        idle_animation = self._build_animation(self.config.get("idle_animation"), "idle", element_type)
        self._apply_animation(element.set_idle_animation, idle_animation)

        remove_animation = self._build_animation(self.config.get("remove_animation"), "remove", element_type)
        self._apply_animation(element.set_remove_animation, remove_animation)

        movement = self._build_movement(self.config.get("movement"))
        movement is not None and element.set_movement(movement)

        return element

    def _build_object(self, element_type: str) -> object | None:
        return OBJECT_BUILDERS.get(element_type, lambda _config: None)(self.config)

    @staticmethod
    def _build_movement(movement_config: Any) -> MovementBase | None:
        movement_map: dict[bool, Callable[[], MovementBase | None]] = {
            True: lambda: resolve_movement(movement_config),
            False: lambda: None,
        }
        return movement_map[isinstance(movement_config, dict)]()

    @staticmethod
    def _apply_animation(setter: Callable[[ShapeAnimation | ArrowAnimation | ImageAnimation], Elements], animation: ShapeAnimation | ArrowAnimation | ImageAnimation | None) -> None:
        animation is not None and setter(animation)

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

        animation_cls = resolve_animation_class(element_type, phase, animation_name)

        return self._instantiate_optional(animation_cls, duration)

    @staticmethod
    def _instantiate_optional(animation_cls: Type[ShapeAnimation | ArrowAnimation | ImageAnimation] | None, duration: float):
        constructors: dict[bool, Callable[[], ShapeAnimation | ArrowAnimation | ImageAnimation | None]] = {
            True: lambda: None,
            False: lambda: animation_cls.build({"duration": duration}, default_duration=duration),
        }
        return constructors[animation_cls is None]()

    @staticmethod
    def _number(value: Any, default: float) -> float:
        try:
            return {True: lambda: default, False: lambda: float(value)}[value is None]()
        except (TypeError, ValueError):
            return default
