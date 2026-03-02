from __future__ import annotations

from typing import Type

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
from .objects.shape_objects import CircleShape, RectangleShape, ShapeObject, SquareShape

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
