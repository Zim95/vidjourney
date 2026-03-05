from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import logging
import re
from typing import Any

from .story_planner import ScenePlan
from .story_planner_constants import (
    AI_CONFIDENCE_BY_SCENE_TYPE,
    AI_ENHANCER_PROMPT,
    AI_ENHANCER_RULES,
    AI_ENHANCER_STOPWORDS,
    AI_FALLBACK_BULLETS_BY_SCENE_TYPE,
    AI_SCENE_TYPE_VISUAL_HINT,
    AI_VISUAL_HINT_KEYWORDS,
    DEFAULT_AI_ENHANCER_CONFIG,
)


logger = logging.getLogger(__name__)


@dataclass
class SceneCompliance:
    changed_structure: bool = False
    added_facts: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "changed_structure": self.changed_structure,
            "added_facts": self.added_facts,
        }


@dataclass
class EnhancedScene:
    scene_id: str
    enhanced_bullets: list[str] = field(default_factory=list)
    emphasis_terms: list[str] = field(default_factory=list)
    staged_reveal: list[str] = field(default_factory=list)
    visual_hint: str = "none"
    confidence: float = 0.0
    compliance: SceneCompliance = field(default_factory=SceneCompliance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "enhanced_bullets": self.enhanced_bullets,
            "emphasis_terms": self.emphasis_terms,
            "staged_reveal": self.staged_reveal,
            "visual_hint": self.visual_hint,
            "confidence": round(self.confidence, 3),
            "compliance": self.compliance.to_dict(),
        }


@dataclass
class StoryPlanAI:
    scenes: list[EnhancedScene] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenes": [scene.to_dict() for scene in self.scenes],
            "metadata": self.metadata,
        }


@dataclass
class AIEnhancerConfig:
    max_bullets_per_scene: int = DEFAULT_AI_ENHANCER_CONFIG["max_bullets_per_scene"]
    max_words_per_bullet: int = DEFAULT_AI_ENHANCER_CONFIG["max_words_per_bullet"]
    max_emphasis_terms: int = DEFAULT_AI_ENHANCER_CONFIG["max_emphasis_terms"]
    max_reveal_steps: int = DEFAULT_AI_ENHANCER_CONFIG["max_reveal_steps"]


class DeterministicSceneAIEnhancer:
    def __init__(self, config: AIEnhancerConfig | None = None):
        self.config = config or AIEnhancerConfig()

    def enhance(self, scene_plan: ScenePlan) -> StoryPlanAI:
        logger.info("Starting scene AI enhancement")
        scenes = [self._enhance_scene(scene.to_dict()) for scene in scene_plan.scenes]
        metadata = {
            "scene_count": len(scenes),
            "enhancer": "deterministic_extractive",
            "prompt": AI_ENHANCER_PROMPT,
            "rules": AI_ENHANCER_RULES,
            "constraints": {
                "max_bullets_per_scene": self.config.max_bullets_per_scene,
                "max_words_per_bullet": self.config.max_words_per_bullet,
                "max_emphasis_terms": self.config.max_emphasis_terms,
                "max_reveal_steps": self.config.max_reveal_steps,
                "structure_locked": True,
            },
        }
        logger.info("Completed scene AI enhancement", extra={"scene_count": len(scenes)})
        return StoryPlanAI(scenes=scenes, metadata=metadata)

    def _enhance_scene(self, scene: dict[str, Any]) -> EnhancedScene:
        scene_id = str(scene.get("scene_id", ""))
        scene_type = str(scene.get("scene_type", "content"))
        source_text = self._collect_source_text(scene)
        source_items = self._collect_list_items(scene)

        bullets = self._build_bullets(scene_type, source_text, source_items)
        emphasis_terms = self._extract_emphasis_terms(source_text)
        staged_reveal = bullets[: self.config.max_reveal_steps]
        visual_hint = self._suggest_visual_hint(scene_type, source_text)
        confidence = self._estimate_confidence(scene_type, bullets)

        return EnhancedScene(
            scene_id=scene_id,
            enhanced_bullets=bullets,
            emphasis_terms=emphasis_terms,
            staged_reveal=staged_reveal,
            visual_hint=visual_hint,
            confidence=confidence,
            compliance=SceneCompliance(changed_structure=False, added_facts=False),
        )

    def _collect_source_text(self, scene: dict[str, Any]) -> str:
        parts: list[str] = []
        for element in scene.get("elements", []):
            payload = element.get("payload", {})
            text = payload.get("text")
            caption = payload.get("caption")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
            if isinstance(caption, str) and caption.strip():
                parts.append(caption.strip())
        return " ".join(parts)

    def _collect_list_items(self, scene: dict[str, Any]) -> list[str]:
        items: list[str] = []
        for element in scene.get("elements", []):
            payload = element.get("payload", {})
            raw_items = payload.get("items")
            if isinstance(raw_items, list):
                items.extend([str(item).strip() for item in raw_items if str(item).strip()])
        return items

    def _build_bullets(self, scene_type: str, source_text: str, source_items: list[str]) -> list[str]:
        strategy_checks = {
            "list": lambda: bool(source_items),
            "fallback": lambda: not source_text.strip(),
            "text": lambda: True,
        }
        strategy_key = next(key for key, check in strategy_checks.items() if check())

        builders = {
            "list": lambda: [self._truncate_words(item) for item in source_items[: self.config.max_bullets_per_scene]],
            "fallback": lambda: [
                self._truncate_words(text)
                for text in AI_FALLBACK_BULLETS_BY_SCENE_TYPE.get(
                    scene_type,
                    AI_FALLBACK_BULLETS_BY_SCENE_TYPE["default"],
                )
            ],
            "text": lambda: self._build_text_bullets(source_text),
        }
        return builders[strategy_key]()

    def _build_text_bullets(self, source_text: str) -> list[str]:
        sentence_parts = [part.strip() for part in re.split(r"[.!?;]\s+", source_text) if part.strip()]
        bullets = sentence_parts[: self.config.max_bullets_per_scene]
        non_empty = {
            True: lambda: bullets,
            False: lambda: [source_text],
        }[bool(bullets)]()
        return [self._truncate_words(bullet) for bullet in non_empty]

    def _extract_emphasis_terms(self, source_text: str) -> list[str]:
        extractors = {
            True: lambda: [],
            False: lambda: self._extract_ranked_terms(source_text),
        }
        return extractors[not source_text.strip()]()

    def _extract_ranked_terms(self, source_text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]+", source_text)
        filtered = [token for token in tokens if len(token) > 3 and token.lower() not in AI_ENHANCER_STOPWORDS]
        frequency = Counter(token.lower() for token in filtered)
        by_frequency = [token for token, _ in frequency.most_common(self.config.max_emphasis_terms)]
        return [token.upper() for token in by_frequency]

    def _suggest_visual_hint(self, scene_type: str, source_text: str) -> str:
        normalized = source_text.lower()
        static_hint = AI_SCENE_TYPE_VISUAL_HINT.get(scene_type)
        static_dispatch = {
            True: lambda: static_hint,
            False: lambda: self._keyword_visual_hint(normalized),
        }
        return str(static_dispatch[static_hint is not None]())

    @staticmethod
    def _keyword_visual_hint(normalized_text: str) -> str:
        return next(
            (hint for hint, keywords in AI_VISUAL_HINT_KEYWORDS.items() if any(keyword in normalized_text for keyword in keywords)),
            "none",
        )

    def _truncate_words(self, text: str) -> str:
        words = text.split()
        truncators = {
            True: lambda: " ".join(words),
            False: lambda: " ".join(words[: self.config.max_words_per_bullet]),
        }
        return truncators[len(words) <= self.config.max_words_per_bullet]()

    @staticmethod
    def _estimate_confidence(scene_type: str, bullets: list[str]) -> float:
        confidence_dispatch = {
            True: lambda: AI_CONFIDENCE_BY_SCENE_TYPE["empty"],
            False: lambda: AI_CONFIDENCE_BY_SCENE_TYPE.get(scene_type, AI_CONFIDENCE_BY_SCENE_TYPE["default"]),
        }
        return float(confidence_dispatch[not bullets]())


def build_story_plan_ai(scene_plan: ScenePlan, config: AIEnhancerConfig | None = None) -> StoryPlanAI:
    logger.debug("build_story_plan_ai helper called")
    return DeterministicSceneAIEnhancer(config=config).enhance(scene_plan)
