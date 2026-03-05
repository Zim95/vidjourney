from __future__ import annotations


DEFAULT_STORY_PLANNER_CONFIG = {
    "max_words_per_scene": 90,
    "max_bullets_per_scene": 5,
    "max_elements_per_scene": 6,
    "large_image_area_threshold": 60000.0,
    "default_transition": "fade",
}


SCENE_HANDLER_BY_ELEMENT_TYPE = {
    "heading": "heading",
    "code_block": "code_block",
    "image": "dynamic_image",
}


FALLBACK_SCENE_HANDLER = "content"


SINGLETON_SCENE_SPECS = {
    "heading": {"scene_type": "title", "force_canvas_clear": True},
    "code_block": {"scene_type": "code", "force_canvas_clear": True},
    "large_image": {"scene_type": "image", "force_canvas_clear": True},
}


DEFAULT_AI_ENHANCER_CONFIG = {
    "max_bullets_per_scene": 5,
    "max_words_per_bullet": 14,
    "max_emphasis_terms": 5,
    "max_reveal_steps": 4,
}


AI_ENHANCER_PROMPT = (
    "You are a constrained scene enhancer. "
    "You may compress and emphasize content inside one pre-bounded scene only. "
    "You must not change scene structure, ordering, transitions, or source mapping."
)


AI_ENHANCER_RULES = [
    "Do not add external facts.",
    "Do not change scene count or scene boundaries.",
    "Do not alter transition or force-canvas-clear behavior.",
    "Do not reorder source elements.",
    "Keep outputs within configured bullet and reveal limits.",
    "If uncertain, use extractive bullets from source text.",
]


AI_ENHANCER_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}


AI_FALLBACK_BULLETS_BY_SCENE_TYPE = {
    "title": ["Section introduction"],
    "image": ["Visual reference"],
    "code": ["Code walkthrough"],
    "default": ["Key point"],
}


AI_VISUAL_HINT_KEYWORDS = {
    "comparison": ["versus", "compare", "trade-off"],
    "timeline": ["first", "then", "before", "after"],
    "flow": ["flow", "pipeline", "process"],
}


AI_SCENE_TYPE_VISUAL_HINT = {
    "code": "flow",
}


AI_CONFIDENCE_BY_SCENE_TYPE = {
    "title": 0.95,
    "code": 0.90,
    "image": 0.80,
    "default": 0.75,
    "empty": 0.20,
}
