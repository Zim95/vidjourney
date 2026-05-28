from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MovementModel:
    type: str
    path: list[list[float]] = field(default_factory=list)
    duration: float = 1.0
    repeat: int = 1
    reverse: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "path": self.path,
            "duration": self.duration,
            "repeat": self.repeat,
            "reverse": self.reverse,
        }


@dataclass
class ElementModel:
    name: str
    type: str
    shape: str | None = None
    url: str | None = None
    text: str | None = None
    position: list[float] | None = None
    from_position: list[float] | None = None
    to_position: list[float] | None = None
    path_points: list[list[float]] | None = None
    size: float | None = None
    fill_color: str | None = None
    border_color: str | None = None
    spawn_animation: dict[str, Any] | None = None
    idle_animation: dict[str, Any] | None = None
    remove_animation: dict[str, Any] | None = None
    movement: MovementModel | None = None

    def to_dict(self) -> dict[str, Any]:
        optional_fields: dict[str, Any] = {
            "shape": self.shape,
            "url": self.url,
            "text": self.text,
            "position": self.position,
            "from": self.from_position,
            "to": self.to_position,
            "path": self.path_points,
            "size": self.size,
            "fill_color": self.fill_color,
            "border_color": self.border_color,
            "spawn_animation": self.spawn_animation,
            "idle_animation": self.idle_animation,
            "remove_animation": self.remove_animation,
            "movement": self.movement.to_dict() if self.movement is not None else None,
        }
        base: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
        }
        base.update({key: value for key, value in optional_fields.items() if value is not None})
        return base


@dataclass
class SequenceStepModel:
    action: str
    target: str | None = None
    targets: list[str] | None = None
    duration: float | None = None

    def to_dict(self) -> dict[str, Any]:
        key_values = {
            "action": self.action,
            "target": self.target,
            "targets": self.targets,
            "duration": self.duration,
        }
        return {key: value for key, value in key_values.items() if value is not None}


@dataclass
class SceneModel:
    elements: list[ElementModel] = field(default_factory=list)
    sequence: list[SequenceStepModel] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "elements": [element.to_dict() for element in self.elements],
            "sequence": [step.to_dict() for step in self.sequence],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def write_json(self, output_path: Path | str) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")
