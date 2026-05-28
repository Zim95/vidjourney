from __future__ import annotations

import subprocess

from .animations import (
    AnimationBase,
    ArrowAnimation,
    BidirectionalDottedArrowRemoveAnimation,
    BidirectionalDottedArrowSpawnAnimation,
    BidirectionalSolidArrowRemoveAnimation,
    BidirectionalSolidArrowSpawnAnimation,
    DottedArrowBidirectionalIdleAnimation,
    DottedArrowUnidirectionalIdleAnimation,
    ImageAnimation,
    ImagePopOutAnimation,
    ImagePopUpAnimation,
    ShapeAnimation,
    ShapePopOutAnimation,
    ShapePopUpAnimation,
    UnidirectionalDottedArrowRemoveAnimation,
    UnidirectionalDottedArrowSpawnAnimation,
    UnidirectionalSolidArrowRemoveAnimation,
    UnidirectionalSolidArrowSpawnAnimation,
)
from .elements import ElementBuilder, Elements
from .movements import BentMovement, CurveMovement, MovementBase, StraightMovement
from .objects import (
    ArrowObject,
    BidirectionalDottedArrow,
    BidirectionalSolidArrow,
    CircleShape,
    DottedArrow,
    ImageObject,
    ObjectBase,
    RectangleShape,
    ShapeObject,
    SolidArrow,
    SquareShape,
    UnidirectionalDottedArrow,
    UnidirectionalSolidArrow,
)

__all__ = [
    "ObjectBase",
    "ShapeObject",
    "CircleShape",
    "SquareShape",
    "RectangleShape",
    "ArrowObject",
    "SolidArrow",
    "DottedArrow",
    "UnidirectionalSolidArrow",
    "BidirectionalSolidArrow",
    "UnidirectionalDottedArrow",
    "BidirectionalDottedArrow",
    "ImageObject",
    "AnimationBase",
    "ShapeAnimation",
    "ShapePopUpAnimation",
    "ShapePopOutAnimation",
    "ArrowAnimation",
    "DottedArrowUnidirectionalIdleAnimation",
    "DottedArrowBidirectionalIdleAnimation",
    "UnidirectionalDottedArrowSpawnAnimation",
    "UnidirectionalDottedArrowRemoveAnimation",
    "UnidirectionalSolidArrowSpawnAnimation",
    "UnidirectionalSolidArrowRemoveAnimation",
    "BidirectionalSolidArrowSpawnAnimation",
    "BidirectionalDottedArrowSpawnAnimation",
    "BidirectionalDottedArrowRemoveAnimation",
    "BidirectionalSolidArrowRemoveAnimation",
    "ImageAnimation",
    "ImagePopUpAnimation",
    "ImagePopOutAnimation",
    "MovementBase",
    "StraightMovement",
    "BentMovement",
    "CurveMovement",
    "Elements",
    "ElementBuilder",
    "run_manim_scene",
]


def run_manim_scene(file_name: str, scene_class: str, quality: str = "ql", preview: bool = True) -> None:
    command = ["manim", f"-p{quality}" if preview else f"-{quality}", file_name, scene_class]
    subprocess.run(command, check=True)
