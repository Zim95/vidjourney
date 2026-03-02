# builtins
import json
import time
from pathlib import Path
from typing import Callable

# third party
from manim import Scene

from .elements import ElementBuilder, Elements


class ManimScene(Scene):
    def construct(self) -> None:
        instructions_path = self._resolve_instructions_path()
        with instructions_path.open("r", encoding="utf-8") as f:
            instructions = json.load(f)

        elements: list[dict] = instructions.get("elements", [])
        sequence: list[dict] = instructions.get("sequence", [])

        built_elements: dict[str, Elements] = {}
        for element_config in elements:
            element = ElementBuilder(element_config).build()
            built_elements[element.name] = element

        action_handlers = self._action_handlers(built_elements)
        for step in sequence:
            action = str(step.get("action", "")).lower()
            action_handlers.get(action, lambda _step: None)(step)

    def _action_handlers(self, built_elements: dict[str, Elements]) -> dict[str, Callable[[dict], None]]:
        return {
            "wait": lambda step: self._wait(step),
            "spawn": lambda step: self._run_with_target(step, built_elements, lambda element: element.spawn(self)),
            "idle": lambda step: self._run_with_target(step, built_elements, lambda element: element.idle(self)),
            "move": lambda step: self._run_with_target(step, built_elements, lambda element: element.move(self)),
            "close": lambda step: self._run_with_target(step, built_elements, lambda element: element.close(self)),
        }

    @staticmethod
    def _wait(step: dict) -> None:
        duration = float(step.get("duration", step.get("seconds", 0.0)))
        time.sleep(max(duration, 0.0))

    @staticmethod
    def _run_with_target(step: dict, built_elements: dict[str, Elements], callback: Callable[[Elements], None]) -> None:
        target = step.get("target")
        valid_target = isinstance(target, str)
        element = built_elements.get(str(target), None)
        (valid_target and element is not None) and callback(element)

    @staticmethod
    def _resolve_instructions_path() -> Path:
        candidates = [
            Path("input/renderer_instructions.json"),
            Path("renderer_instructions.json"),
        ]
        return next(filter(lambda candidate: candidate.exists() and candidate.is_file(), candidates), candidates[0])
