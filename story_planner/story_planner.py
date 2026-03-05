from __future__ import annotations

from dataclasses import dataclass, field
import logging
from math import prod
from typing import Any, Callable

from ingestion.metadata_layer import MetadataEnrichedDocument
from .story_planner_constants import (
    DEFAULT_STORY_PLANNER_CONFIG,
    FALLBACK_SCENE_HANDLER,
    SCENE_HANDLER_BY_ELEMENT_TYPE,
    SINGLETON_SCENE_SPECS,
)


logger = logging.getLogger(__name__)


@dataclass
class StoryPlannerConfig:
    max_words_per_scene: int = DEFAULT_STORY_PLANNER_CONFIG["max_words_per_scene"]
    max_bullets_per_scene: int = DEFAULT_STORY_PLANNER_CONFIG["max_bullets_per_scene"]
    max_elements_per_scene: int = DEFAULT_STORY_PLANNER_CONFIG["max_elements_per_scene"]
    large_image_area_threshold: float = DEFAULT_STORY_PLANNER_CONFIG["large_image_area_threshold"]
    default_transition: str = DEFAULT_STORY_PLANNER_CONFIG["default_transition"]


@dataclass
class PlannedScene:
    scene_id: str
    chapter_title: str
    chapter_number: str | None
    section_path: list[str]
    scene_type: str
    transition: str
    force_canvas_clear: bool
    word_estimate: int
    bullet_count: int
    elements: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "chapter_title": self.chapter_title,
            "chapter_number": self.chapter_number,
            "section_path": self.section_path,
            "scene_type": self.scene_type,
            "transition": self.transition,
            "force_canvas_clear": self.force_canvas_clear,
            "word_estimate": self.word_estimate,
            "bullet_count": self.bullet_count,
            "elements": self.elements,
        }


@dataclass
class ScenePlan:
    scenes: list[PlannedScene] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenes": [scene.to_dict() for scene in self.scenes],
            "metadata": self.metadata,
        }


@dataclass
class _SplitState:
    planned_scenes: list[PlannedScene] = field(default_factory=list)
    scene_counter: int = 1
    current_scene: PlannedScene | None = None


class DeterministicSceneSplitter:
    def __init__(self, config: StoryPlannerConfig | None = None):
        self.config = config or StoryPlannerConfig()

    def split(self, enriched_document: MetadataEnrichedDocument) -> ScenePlan:
        logger.info("Starting deterministic scene split")
        state = _SplitState()
        for chapter_index, chapter in enumerate(enriched_document.chapters):
            self._split_chapter(chapter_index, chapter, state)

        metadata = {
            "scene_count": len(state.planned_scenes),
            "planner": "deterministic_structural",
            "config": {
                "max_words_per_scene": self.config.max_words_per_scene,
                "max_bullets_per_scene": self.config.max_bullets_per_scene,
                "max_elements_per_scene": self.config.max_elements_per_scene,
                "large_image_area_threshold": self.config.large_image_area_threshold,
                "default_transition": self.config.default_transition,
            },
        }

        logger.info("Completed deterministic scene split", extra={"scene_count": len(state.planned_scenes)})
        return ScenePlan(scenes=state.planned_scenes, metadata=metadata)

    def _split_chapter(self, chapter_index: int, chapter: dict[str, Any], state: _SplitState) -> None:
        chapter_title = str(chapter.get("title", ""))
        chapter_number = chapter.get("number")
        elements = list(chapter.get("elements", []))
        handlers = self._handlers()

        for element_index, element in enumerate(elements):
            handler_key = self._resolve_handler_key(element)
            handlers[handler_key](
                chapter_index,
                element_index,
                element,
                chapter_title,
                chapter_number,
                state,
            )

        self._flush_scene(state)

    def _handlers(self) -> dict[str, Callable[[int, int, dict[str, Any], str, str | None, _SplitState], None]]:
        return {
            "heading": self._handle_heading,
            "code_block": self._handle_code_block,
            "large_image": self._handle_large_image,
            "content": self._handle_content,
        }

    def _resolve_handler_key(self, element: dict[str, Any]) -> str:
        element_type = str(element.get("type", ""))
        resolvers = {
            "dynamic_image": lambda current_element: {
                True: "large_image",
                False: "content",
            }[self._is_large_image(current_element)],
        }
        base_key = SCENE_HANDLER_BY_ELEMENT_TYPE.get(element_type, FALLBACK_SCENE_HANDLER)
        resolver = resolvers.get(base_key, lambda _current_element: base_key)
        return resolver(element)

    def _handle_heading(
        self,
        chapter_index: int,
        element_index: int,
        element: dict[str, Any],
        chapter_title: str,
        chapter_number: str | None,
        state: _SplitState,
    ) -> None:
        spec = SINGLETON_SCENE_SPECS["heading"]
        self._flush_scene(state)
        self._append_singleton_scene(
            scene_type=str(spec["scene_type"]),
            force_canvas_clear=bool(spec["force_canvas_clear"]),
            chapter_index=chapter_index,
            element_index=element_index,
            element=element,
            chapter_title=chapter_title,
            chapter_number=chapter_number,
            state=state,
        )

    def _handle_code_block(
        self,
        chapter_index: int,
        element_index: int,
        element: dict[str, Any],
        chapter_title: str,
        chapter_number: str | None,
        state: _SplitState,
    ) -> None:
        spec = SINGLETON_SCENE_SPECS["code_block"]
        self._flush_scene(state)
        self._append_singleton_scene(
            scene_type=str(spec["scene_type"]),
            force_canvas_clear=bool(spec["force_canvas_clear"]),
            chapter_index=chapter_index,
            element_index=element_index,
            element=element,
            chapter_title=chapter_title,
            chapter_number=chapter_number,
            state=state,
        )

    def _handle_large_image(
        self,
        chapter_index: int,
        element_index: int,
        element: dict[str, Any],
        chapter_title: str,
        chapter_number: str | None,
        state: _SplitState,
    ) -> None:
        spec = SINGLETON_SCENE_SPECS["large_image"]
        self._flush_scene(state)
        self._append_singleton_scene(
            scene_type=str(spec["scene_type"]),
            force_canvas_clear=bool(spec["force_canvas_clear"]),
            chapter_index=chapter_index,
            element_index=element_index,
            element=element,
            chapter_title=chapter_title,
            chapter_number=chapter_number,
            state=state,
        )

    def _handle_content(
        self,
        chapter_index: int,
        element_index: int,
        element: dict[str, Any],
        chapter_title: str,
        chapter_number: str | None,
        state: _SplitState,
    ) -> None:
        for variant in self._expand_element_variants(element):
            self._append_content_variant(
                chapter_index=chapter_index,
                element_index=element_index,
                variant=variant,
                chapter_title=chapter_title,
                chapter_number=chapter_number,
                section_source=element,
                state=state,
            )

    def _append_singleton_scene(
        self,
        scene_type: str,
        force_canvas_clear: bool,
        chapter_index: int,
        element_index: int,
        element: dict[str, Any],
        chapter_title: str,
        chapter_number: str | None,
        state: _SplitState,
    ) -> None:
        scene = self._new_scene(
            scene_counter=state.scene_counter,
            chapter_title=chapter_title,
            chapter_number=chapter_number,
            section_path=self._section_path(element),
            scene_type=scene_type,
            force_canvas_clear=force_canvas_clear,
        )
        scene.elements.append(self._source_ref(chapter_index, element_index, element))
        scene.word_estimate += self._estimate_words(element)
        scene.bullet_count += self._estimate_bullets(element)
        state.planned_scenes.append(scene)
        state.scene_counter += 1

    def _append_content_variant(
        self,
        chapter_index: int,
        element_index: int,
        variant: dict[str, Any],
        chapter_title: str,
        chapter_number: str | None,
        section_source: dict[str, Any],
        state: _SplitState,
    ) -> None:
        variant_words = self._estimate_words(variant)
        variant_bullets = self._estimate_bullets(variant)

        {
            True: lambda: self._start_content_scene(state, chapter_title, chapter_number, section_source),
            False: lambda: None,
        }[state.current_scene is None]()

        would_exceed_budget = (
            len(state.current_scene.elements) >= self.config.max_elements_per_scene
            or (state.current_scene.word_estimate + variant_words) > self.config.max_words_per_scene
            or (state.current_scene.bullet_count + variant_bullets) > self.config.max_bullets_per_scene
        )

        {
            True: lambda: self._reset_content_scene(state, chapter_title, chapter_number, section_source),
            False: lambda: None,
        }[would_exceed_budget]()

        state.current_scene.elements.append(self._source_ref(chapter_index, element_index, variant))
        state.current_scene.word_estimate += variant_words
        state.current_scene.bullet_count += variant_bullets

    def _start_content_scene(
        self,
        state: _SplitState,
        chapter_title: str,
        chapter_number: str | None,
        section_source: dict[str, Any],
    ) -> None:
        state.current_scene = self._new_scene(
            scene_counter=state.scene_counter,
            chapter_title=chapter_title,
            chapter_number=chapter_number,
            section_path=self._section_path(section_source),
            scene_type="content",
            force_canvas_clear=False,
        )

    def _reset_content_scene(
        self,
        state: _SplitState,
        chapter_title: str,
        chapter_number: str | None,
        section_source: dict[str, Any],
    ) -> None:
        self._flush_scene(state)
        self._start_content_scene(state, chapter_title, chapter_number, section_source)

    @staticmethod
    def _flush_scene(state: _SplitState) -> None:
        if state.current_scene is None:
            return
        state.planned_scenes.append(state.current_scene)
        state.scene_counter += 1
        state.current_scene = None

    def _new_scene(
        self,
        scene_counter: int,
        chapter_title: str,
        chapter_number: str | None,
        section_path: list[str],
        scene_type: str,
        force_canvas_clear: bool,
    ) -> PlannedScene:
        return PlannedScene(
            scene_id=f"scene_{scene_counter:04d}",
            chapter_title=chapter_title,
            chapter_number=chapter_number,
            section_path=section_path,
            scene_type=scene_type,
            transition=self.config.default_transition,
            force_canvas_clear=force_canvas_clear,
            word_estimate=0,
            bullet_count=0,
            elements=[],
        )

    @staticmethod
    def _section_path(element: dict[str, Any]) -> list[str]:
        value = element.get("section_path")
        return list(value) if isinstance(value, list) else []

    def _is_large_image(self, element: dict[str, Any]) -> bool:
        bbox = element.get("bbox")
        is_valid_bbox = isinstance(bbox, list) and len(bbox) == 4
        validity_dispatch = {
            True: lambda: self._bbox_area_exceeds_threshold(bbox),
            False: lambda: True,
        }
        return bool(validity_dispatch[is_valid_bbox]())

    def _bbox_area_exceeds_threshold(self, bbox: list[Any]) -> bool:
        x0, y0, x1, y1 = bbox
        try:
            width = max(float(x1) - float(x0), 0.0)
            height = max(float(y1) - float(y0), 0.0)
            area = prod([width, height])
        except (TypeError, ValueError):
            return True
        return area >= self.config.large_image_area_threshold

    def _expand_element_variants(self, element: dict[str, Any]) -> list[dict[str, Any]]:
        element_type = str(element.get("type", ""))
        type_dispatch = {
            "list": self._expand_list_variants,
        }
        return type_dispatch.get(element_type, lambda current_element: [current_element])(element)

    def _expand_list_variants(self, element: dict[str, Any]) -> list[dict[str, Any]]:
        items = element.get("items")
        validity_dispatch = {
            True: lambda: self._slice_list_variants(element, items),
            False: lambda: [element],
        }
        return validity_dispatch[isinstance(items, list)]()

    def _slice_list_variants(self, element: dict[str, Any], items: list[Any]) -> list[dict[str, Any]]:
        small_enough_dispatch = {
            True: lambda: [element],
            False: lambda: self._build_list_slices(element, items),
        }
        return small_enough_dispatch[len(items) <= self.config.max_bullets_per_scene]()

    def _build_list_slices(self, element: dict[str, Any], items: list[Any]) -> list[dict[str, Any]]:
        variants: list[dict[str, Any]] = []
        start = 0
        while start < len(items):
            stop = start + self.config.max_bullets_per_scene
            cloned = dict(element)
            cloned_items = [str(item) for item in items[start:stop]]
            cloned["items"] = cloned_items
            cloned["__derived"] = {
                "type": "list_slice",
                "start_index": start,
                "end_index": min(stop, len(items)),
            }
            variants.append(cloned)
            start = stop
        return variants

    @staticmethod
    def _estimate_words(element: dict[str, Any]) -> int:
        parts: list[str] = []
        text = element.get("text")
        if isinstance(text, str):
            parts.append(text)
        caption = element.get("caption")
        if isinstance(caption, str):
            parts.append(caption)
        items = element.get("items")
        if isinstance(items, list):
            parts.extend([str(item) for item in items])
        combined = " ".join(parts).strip()
        return len(combined.split()) if combined else 0

    @staticmethod
    def _estimate_bullets(element: dict[str, Any]) -> int:
        items = element.get("items")
        return len(items) if isinstance(items, list) else 0

    @staticmethod
    def _source_ref(chapter_index: int, element_index: int, element: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": {
                "chapter_index": chapter_index,
                "element_index": element_index,
            },
            "type": element.get("type"),
            "page": element.get("page"),
            "section_path": element.get("section_path"),
            "payload": element,
        }


def build_scene_plan(
    metadata_enriched_document: MetadataEnrichedDocument,
    config: StoryPlannerConfig | None = None,
) -> ScenePlan:
    logger.debug("build_scene_plan helper called")
    return DeterministicSceneSplitter(config=config).split(metadata_enriched_document)
