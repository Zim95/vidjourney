from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path

import os


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIR / "configuration.cfg"
CONFIG = ConfigParser()
if CONFIG_FILE.exists() and CONFIG_FILE.is_file():
	CONFIG.read(CONFIG_FILE, encoding="utf-8")


def _cfg_text(section: str, key: str, fallback: str) -> str:
	if CONFIG.has_section(section) and CONFIG.has_option(section, key):
		return CONFIG.get(section, key)
	return fallback


def _cfg_int(section: str, key: str, fallback: int) -> int:
	try:
		return max(1, int(_cfg_text(section, key, str(fallback))))
	except (TypeError, ValueError):
		return fallback


def _cfg_path(section: str, key: str, fallback: str) -> Path:
	return Path(_cfg_text(section, key, fallback))


SCENES_DIR = _cfg_path("scenes", "scenes_dir", "pipeline/scenes")
SCENE_FILE_NAME = _cfg_text("scenes", "default_scene_file", "dsl_instructions.scene")

RENDER_DIR = _cfg_path("render", "render_dir", "pipeline/render")
RENDER_FILE_NAME = _cfg_text("render", "default_render_file", "dsl_instructions.render.json")
SCENE_TO_RENDER_MAX_WORKERS = _cfg_int("render", "max_workers", 4)

DSL_SCENE_FILE = SCENES_DIR / SCENE_FILE_NAME
DSL_GRAMMAR_FILE = _cfg_path("scenes", "dsl_grammar_file", "src/dsl/renderer_dsl.lark")
RENDERER_INSTRUCTIONS_FILE = RENDER_DIR / RENDER_FILE_NAME

MANIM_PYTHON = _cfg_text("manim", "python", ".venv/bin/python")
MANIM_SCENE_FILE = _cfg_text("manim", "scene_file", "src/renderer/manim/manim_runner.py")
MANIM_SCENE_CLASS = _cfg_text("manim", "scene_class", "ManimScene")
MANIM_QUALITY = _cfg_text("manim", "quality", "ql")
MANIM_PREVIEW = _cfg_text("manim", "preview", "true")
RENDER_TO_MANIM_MAX_WORKERS = _cfg_int("manim", "max_workers", 4)


INGEST_MAX_WORKERS = os.cpu_count() or _cfg_int("ingestion", "max_workers", 1)
INGEST_GLOBAL_READING_ORDER_STRIDE = 100_000

# Ingestion table detection thresholds
INGESTION_TABLE_Y_TOLERANCE = float(_cfg_text("ingestion", "table_y_tolerance", "3.0"))
INGESTION_TABLE_X_CLUSTER_TOLERANCE = float(_cfg_text("ingestion", "table_x_cluster_tolerance", "8.0"))
INGESTION_TABLE_ROW_SPACING_VARIANCE = float(_cfg_text("ingestion", "table_row_spacing_variance", "2.5"))
INGESTION_TABLE_WIDTH_RATIO = float(_cfg_text("ingestion", "table_width_ratio", "0.75"))
INGESTION_TABLE_SCORE_THRESHOLD = float(_cfg_text("ingestion", "table_score_threshold", "3.0"))
# Ingestion code detection thresholds
INGESTION_CODE_SQL_KEYWORD_HITS = int(_cfg_text("ingestion", "code_sql_keyword_hits", "4"))
INGESTION_CODE_SYMBOL_DENSITY_MIN = float(_cfg_text("ingestion", "code_symbol_density_min", "0.015"))
INGESTION_CODE_SYMBOL_DENSITY_STRONG = float(_cfg_text("ingestion", "code_symbol_density_strong", "0.03"))
INGESTION_CODE_MIN_LINE_COUNT = int(_cfg_text("ingestion", "code_min_line_count", "3"))
INGESTION_CODE_DEMOTE_SYMBOL_DENSITY = float(_cfg_text("ingestion", "code_demote_symbol_density", "0.02"))
INGESTION_CODE_DEMOTE_SQL_HITS = int(_cfg_text("ingestion", "code_demote_sql_hits", "2"))
INGESTION_CODE_PROSE_MIN_LINE_LENGTH = int(_cfg_text("ingestion", "code_prose_min_line_length", "8"))
INGESTION_CODE_PROSE_CONNECTOR_KEYWORDS = _cfg_text("ingestion", "code_prose_connector_keywords", "which,because,therefore,works,calculate,efficiently,load,find").split(",")

# Code block rendering
INGESTION_CODE_BLOCKS_DIR = _cfg_path("ingestion", "code_blocks_dir", "pipeline/sections/resources/code_blocks")
INGESTION_CODE_BLOCK_IMAGES_DIR = _cfg_path("ingestion", "code_block_images_dir", "pipeline/sections/resources/code_block_images")
INGESTION_CODE_BLOCK_FONT_SIZE = _cfg_int("ingestion", "code_block_font_size", 16)
INGESTION_CODE_BLOCK_LINE_NUMBERS = _cfg_text("ingestion", "code_block_line_numbers", "false").lower() == "true"
INGESTION_CODE_BLOCK_STYLE = _cfg_text("ingestion", "code_block_style", "monokai")


# Ollama / embeddings configuration
OLLAMA_URL = _cfg_text("ml", "ollama_url", "http://localhost:11434/api/embeddings")
OLLAMA_MODEL = _cfg_text("ml", "embedding_model", "nomic-embed-text")
ML_EMBEDDING_BATCH_SIZE = _cfg_int("ml", "embedding_batch_size", 50)

# Model training
ML_MODELS_DIR = _cfg_path("ml", "models_dir", "models")
ML_MODEL_FILENAME = _cfg_text("ml", "model_filename", "code_rf.joblib")
ML_TRAINING_DATA_DIR = _cfg_path("ml", "training_data_dir", "src/ingestion/ml/training_code_snippets")
ML_N_ESTIMATORS = _cfg_int("ml", "n_estimators", 200)
ML_TEST_SIZE = float(_cfg_text("ml", "test_size", "0.2"))
ML_RANDOM_STATE = _cfg_int("ml", "random_state", 42)

# Inference threshold for line-level code detection
ML_CODE_LINE_THRESHOLD = float(_cfg_text("ml", "code_line_threshold", "0.4"))

# Grouping configuration
GROUPING_SECTIONS_DIR = _cfg_path("grouping", "sections_dir", "pipeline/sections")
GROUPING_SCENE_GROUPS_DIR = _cfg_path("grouping", "scene_groups_dir", "pipeline/groups/scene_groups")
GROUPING_TIMELINES_DIR = _cfg_path("grouping", "timelines_dir", "pipeline/groups/timelines")
GROUPING_CHARS_PER_TOKEN = _cfg_int("grouping", "chars_per_token", 4)

# Timeline timing
GROUPING_WORDS_PER_MINUTE = float(_cfg_text("grouping", "words_per_minute", "150.0"))
GROUPING_MIN_SCENE_DURATION = float(_cfg_text("grouping", "min_scene_duration", "4.0"))
GROUPING_SPAWN_GAP = float(_cfg_text("grouping", "spawn_gap", "0.5"))
GROUPING_ARROW_DURATION = float(_cfg_text("grouping", "arrow_duration", "1.5"))
GROUPING_FADE_DURATION = float(_cfg_text("grouping", "fade_duration", "0.5"))
GROUPING_HOLD_AFTER_ARROW = float(_cfg_text("grouping", "hold_after_arrow", "0.5"))

# Gemini configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = _cfg_text("gemini", "model", "gemini-2.0-flash-lite")
GEMINI_MAX_TOKENS_PER_MINUTE = _cfg_int("gemini", "max_tokens_per_minute", 250000)
GEMINI_MAX_REQUESTS_PER_MINUTE = _cfg_int("gemini", "max_requests_per_minute", 10)
GEMINI_MAX_REQUESTS_PER_DAY = _cfg_int("gemini", "max_requests_per_day", 500)
GEMINI_MAX_RETRIES = _cfg_int("gemini", "max_retries", 3)

# Ollama LLM configuration
OLLAMA_BASE_URL = _cfg_text("ollama", "base_url", "http://localhost:11434")
OLLAMA_CHAT_MODEL = _cfg_text("ollama", "chat_model", "llama3.1:8b")
OLLAMA_MAX_RETRIES = _cfg_int("ollama", "max_retries", 3)
