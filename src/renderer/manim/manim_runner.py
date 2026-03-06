# builtins
import json
import os
import time
from pathlib import Path
from typing import Callable

# third party
from manim import Animation, Scene

from src.renderer.manim.elements import ElementBuilder, Elements


class ManimScene(Scene):
    IDLE_WAIT_TICK_SECONDS = 0.2

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
        active_idle_targets: set[str] = set()
        for step in sequence:
            action = str(step.get("action", "")).lower()
            action_handlers.get(action, lambda _step, _active_idle_targets: None)(step, active_idle_targets)

    def _action_handlers(self, built_elements: dict[str, Elements]) -> dict[str, Callable[[dict, set[str]], None]]:
        return {
            "wait": lambda step, active_idle_targets: self._wait(step, built_elements, active_idle_targets),
            "spawn": lambda step, active_idle_targets: self._spawn(step, built_elements, active_idle_targets),
            "idle": lambda step, active_idle_targets: self._idle(step, built_elements, active_idle_targets),
            "move": lambda step, active_idle_targets: self._move(step, built_elements, active_idle_targets),
            "close": lambda step, active_idle_targets: self._close(step, built_elements, active_idle_targets),
        }

    def _wait(self, step: dict, built_elements: dict[str, Elements], active_idle_targets: set[str]) -> None:
        duration = float(step.get("duration", step.get("seconds", 0.0)))
        bounded_duration = max(duration, 0.0)
        self._play_background_for_duration(bounded_duration, built_elements, active_idle_targets)

    def _play_background_for_duration(self, duration: float, built_elements: dict[str, Elements], active_idle_targets: set[str]) -> None:
        remaining = max(duration, 0.0)
        tick = max(float(self.IDLE_WAIT_TICK_SECONDS), 0.01)

        while remaining > 0.0:
            chunk = min(tick, remaining)
            idle_clips = self._idle_clips(built_elements, active_idle_targets)
            idle_clips and self.play(*idle_clips, run_time=chunk) or time.sleep(chunk)
            remaining -= chunk

    def _spawn(self, step: dict, built_elements: dict[str, Elements], active_idle_targets: set[str]) -> None:
        spawn_targets = self._targets_from_step(step)
        spawn_elements = [built_elements.get(target) for target in spawn_targets]
        spawn_elements = [element for element in spawn_elements if element is not None]
        if not spawn_elements:
            return

        spawn_pairs = [(element, element.spawn_clip()) for element in spawn_elements]
        spawn_pairs = [(element, clip) for element, clip in spawn_pairs if clip is not None]
        if not spawn_pairs:
            return

        spawn_run_time = max((element.spawn_duration() for element, _ in spawn_pairs), default=0.0)
        spawn_clips = [clip for _, clip in spawn_pairs]
        clips = [*spawn_clips, *self._idle_clips(built_elements, active_idle_targets)]
        self.play(*clips, run_time=spawn_run_time)

    def _idle(self, step: dict, built_elements: dict[str, Elements], active_idle_targets: set[str]) -> None:
        target = step.get("target")
        valid_target = isinstance(target, str)
        element = built_elements.get(str(target), None)
        valid_target and element is not None and element.idle_animation is not None and active_idle_targets.add(str(target))

    def _move(self, step: dict, built_elements: dict[str, Elements], active_idle_targets: set[str]) -> None:
        element = self._element_from_step(step, built_elements)
        if element is None:
            return

        move_clip = element.move_clip()
        if move_clip is None:
            return

        clips = [move_clip, *self._idle_clips(built_elements, active_idle_targets)]
        self.play(*clips, run_time=max(element.move_duration(), 0.0))

    def _close(self, step: dict, built_elements: dict[str, Elements], active_idle_targets: set[str]) -> None:
        close_targets = self._targets_from_step(step)

        close_elements = [built_elements.get(target) for target in close_targets]
        close_elements = [element for element in close_elements if element is not None]
        if not close_elements:
            return

        for target in close_targets:
            active_idle_targets.discard(target)

        close_pairs = [(element, element.close_clip()) for element in close_elements]
        close_pairs = [(element, clip) for element, clip in close_pairs if clip is not None]
        if not close_pairs:
            return

        close_run_time = max((element.close_duration() for element, _ in close_pairs), default=0.0)
        close_clips = [clip for _, clip in close_pairs]
        clips = [*close_clips, *self._idle_clips(built_elements, active_idle_targets)]
        self.play(*clips, run_time=close_run_time)
        for element, _ in close_pairs:
            element._mobject is not None and setattr(element, "_mobject", None)

    @staticmethod
    def _targets_from_step(step: dict) -> list[str]:
        target_entries = step.get("targets")
        multiple_targets = isinstance(target_entries, list)
        targets = [str(target) for target in target_entries if isinstance(target, str)] if multiple_targets else []
        if not targets:
            fallback_target = step.get("target")
            isinstance(fallback_target, str) and targets.append(str(fallback_target))
        return targets

    @staticmethod
    def _element_from_step(step: dict, built_elements: dict[str, Elements]) -> Elements | None:
        target = step.get("target")
        valid_target = isinstance(target, str)
        return built_elements.get(str(target), None) if valid_target else None

    @staticmethod
    def _idle_clips(built_elements: dict[str, Elements], active_idle_targets: set[str]) -> list[Animation]:
        clips: list[Animation] = []
        for target in active_idle_targets:
            element = built_elements.get(target)
            clip = element is not None and element.idle_clip() or None
            clip is not None and clips.append(clip)
        return clips

    @staticmethod
    def _resolve_instructions_path() -> Path:
        environment_path = os.getenv("RENDERER_INSTRUCTIONS_FILE")
        if environment_path:
            return Path(environment_path)

        pipeline_render_dir = Path("pipeline/render")
        if pipeline_render_dir.exists() and pipeline_render_dir.is_dir():
            render_files = sorted(pipeline_render_dir.glob("*.render.json"))
            if render_files:
                return render_files[0]

        candidates = [
            Path("pipeline/render/dsl_instructions.render.json"),
            Path("renderer_instructions.json"),
        ]
        return next(filter(lambda candidate: candidate.exists() and candidate.is_file(), candidates), candidates[0])
