from __future__ import annotations

from typing import Any, Callable, Type

from .animations.arrow_animations import (
    ArrowAnimation,
    BidirectionalDottedArrowRemoveAnimation,
    BidirectionalDottedArrowSpawnAnimation,
    BidirectionalSolidArrowRemoveAnimation,
    BidirectionalSolidArrowSpawnAnimation,
    DottedArrowBidirectionalIdleAnimation,
    DottedArrowUnidirectionalIdleAnimation,
    UnidirectionalDottedArrowRemoveAnimation,
    UnidirectionalDottedArrowSpawnAnimation,
    UnidirectionalSolidArrowRemoveAnimation,
    UnidirectionalSolidArrowSpawnAnimation,
)
from .animations.image_animations import ImageAnimation, ImagePopOutAnimation, ImagePopUpAnimation
from .animations.shape_animations import ShapeAnimation, ShapePopOutAnimation, ShapePopUpAnimation
from .objects.arrow_objects import (
    ArrowObject,
    BidirectionalDottedArrow,
    BidirectionalSolidArrow,
    DottedArrow,
    SolidArrow,
    UnidirectionalDottedArrow,
    UnidirectionalSolidArrow,
)
from .objects.image_objects import ImageObject
from .objects.shape_objects import CircleShape, RectangleShape, ShapeObject, SquareShape
from .movements import BentMovement, CurveMovement, MovementBase, StraightMovement

DEFAULT_SHAPE_OBJECT: Type[ShapeObject] = SquareShape
DEFAULT_ARROW_OBJECT: Type[ArrowObject] = UnidirectionalDottedArrow

SHAPE_OBJECT_MAP: dict[str, Type[ShapeObject]] = {
    "circle": CircleShape,
    "rectangle": RectangleShape,
    "square": SquareShape,
}

ARROW_OBJECT_MAP: dict[str, Type[ArrowObject]] = {
    "unidirectional_solid": UnidirectionalSolidArrow,
    "bidirectional_solid": BidirectionalSolidArrow,
    "bidirectional_dotted": BidirectionalDottedArrow,
    "solid": SolidArrow,
    "dotted": DottedArrow,
    "unidirectional_dotted": UnidirectionalDottedArrow,
}

SHAPE_PHASE_ANIMATION_MAP: dict[str, Type[ShapeAnimation]] = {
    "spawn": ShapePopUpAnimation,
    "remove": ShapePopOutAnimation,
}

IMAGE_PHASE_ANIMATION_MAP: dict[str, Type[ImageAnimation]] = {
    "spawn": ImagePopUpAnimation,
    "remove": ImagePopOutAnimation,
}

ARROW_PHASE_ANIMATION_MAP: dict[str, dict[str, Type[ArrowAnimation]]] = {
    "idle": {
        "dotted_unidirectional_idle": DottedArrowUnidirectionalIdleAnimation,
        "dotted_bidirectional_idle": DottedArrowBidirectionalIdleAnimation,
    },
    "spawn": {
        "unidirectional_dotted_spawn": UnidirectionalDottedArrowSpawnAnimation,
        "unidirectional_solid_spawn": UnidirectionalSolidArrowSpawnAnimation,
        "bidirectional_dotted_spawn": BidirectionalDottedArrowSpawnAnimation,
        "bidirectional_solid_spawn": BidirectionalSolidArrowSpawnAnimation,
    },
    "remove": {
        "unidirectional_dotted_remove": UnidirectionalDottedArrowRemoveAnimation,
        "unidirectional_solid_remove": UnidirectionalSolidArrowRemoveAnimation,
        "bidirectional_dotted_remove": BidirectionalDottedArrowRemoveAnimation,
        "bidirectional_solid_remove": BidirectionalSolidArrowRemoveAnimation,
    },
}

OBJECT_BUILDERS: dict[str, Callable[[dict[str, Any]], object | None]] = {
    "shape": lambda config: SHAPE_OBJECT_MAP.get(str(config.get("shape", "square")).lower(), DEFAULT_SHAPE_OBJECT).build(config),
    "arrow": lambda config: ARROW_OBJECT_MAP.get(
        str(config.get("shape", config.get("arrow_type", "unidirectional_dotted"))).lower(),
        DEFAULT_ARROW_OBJECT,
    ).build(config),
    "image": lambda config: ImageObject.build(config),
}

OBJECT_APPLIERS: dict[str, Callable[[Any, object | None], None]] = {
    "shape": lambda element, built_object: built_object is not None and element.set_shape(built_object),
    "arrow": lambda element, built_object: built_object is not None and element.set_shape(built_object),
    "image": lambda element, built_object: built_object is not None and element.set_image_object(built_object),
}

MOVEMENT_BUILDERS: dict[str, Callable[[dict[str, Any]], MovementBase]] = {
    "straight": lambda config: StraightMovement.build(config),
    "bent": lambda config: BentMovement.build(config),
    "upwardsbent": lambda config: BentMovement.build(config, bend_direction="up"),
    "downwardsbent": lambda config: BentMovement.build(config, bend_direction="down"),
    "curve": lambda config: CurveMovement.build(config),
    "upwardscurve": lambda config: CurveMovement.build(config, curve_direction="up"),
    "downwardscurve": lambda config: CurveMovement.build(config, curve_direction="down"),
}

ANIMATION_CLASS_RESOLVERS: dict[str, Callable[[str, str], Type[ShapeAnimation | ArrowAnimation | ImageAnimation] | None]] = {
    "shape": lambda phase, _animation_name: SHAPE_PHASE_ANIMATION_MAP.get(phase),
    "image": lambda phase, _animation_name: IMAGE_PHASE_ANIMATION_MAP.get(phase),
    "arrow": lambda phase, animation_name: ARROW_PHASE_ANIMATION_MAP.get(phase, {}).get(animation_name),
}


def resolve_animation_class(element_type: str, phase: str, animation_name: str) -> Type[ShapeAnimation | ArrowAnimation | ImageAnimation] | None:
    return ANIMATION_CLASS_RESOLVERS.get(element_type, lambda _phase, _name: None)(phase, animation_name)


def resolve_movement(config: dict[str, Any]) -> MovementBase | None:
    movement_type = str(config.get("type", "straight")).lower()
    builder = MOVEMENT_BUILDERS.get(movement_type, MOVEMENT_BUILDERS.get("straight"))
    return builder(config) if builder is not None else None
