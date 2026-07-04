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


# Pipeline executor pools
PIPELINE_THREAD_WORKERS = _cfg_int("pipeline", "thread_workers", 4)
PIPELINE_PROCESS_WORKERS = _cfg_int("pipeline", "process_workers", 4)

# Scroll render shells out to manim with this interpreter
MANIM_PYTHON = _cfg_text("manim", "python", ".venv/bin/python")


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
INGESTION_CODE_BLOCK_IMAGE_PAD = _cfg_int("ingestion", "code_block_image_pad", 20)


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
# Review gate: code lines whose proba is within this margin of the threshold are
# "borderline" and get surfaced for human confirmation.
ML_CODE_LINE_CONFIDENCE_MARGIN = float(_cfg_text("ml", "code_line_confidence_margin", "0.15"))

# Grouping configuration
GROUPING_SECTIONS_DIR = _cfg_path("grouping", "sections_dir", "pipeline/sections")
GROUPING_CONTENT_GROUPS_DIR = _cfg_path("grouping", "content_groups_dir", "pipeline/groups/content_groups")
# Review gate output: sections that passed (auto or human) the review gate.
GROUPING_APPROVED_DIR = _cfg_path("grouping", "approved_dir", "pipeline/groups/approved")

# Narration
GROUPING_PIPER_MODEL = _cfg_text("grouping", "piper_model", "en_US-lessac-medium.onnx")
GROUPING_PIPER_SPEAKER_ID = _cfg_int("grouping", "piper_speaker_id", 0)
GROUPING_PIPER_LENGTH_SCALE = float(_cfg_text("grouping", "piper_length_scale", "1.0"))

# Part packaging
GROUPING_BOOK_TITLE = _cfg_text("grouping", "book_title", "Book")
GROUPING_PART_MIN_DURATION_MINUTES = float(_cfg_text("grouping", "part_min_duration_minutes", "10.0"))

# Ollama LLM configuration (chat — optional part-title generator)
OLLAMA_BASE_URL = _cfg_text("ollama", "base_url", "http://localhost:11434")
OLLAMA_CHAT_MODEL = _cfg_text("ollama", "chat_model", "llama3.1:8b")
OLLAMA_MAX_RETRIES = _cfg_int("ollama", "max_retries", 3)

# YouTube upload / scheduling
YOUTUBE_CLIENT_SECRETS_FILE = _cfg_text("youtube", "client_secrets_file", "")
YOUTUBE_TOKEN_FILE = _cfg_path("youtube", "token_file", "pipeline/descriptions/.youtube_token.json")
YOUTUBE_PARTS_DIR = _cfg_path("youtube", "parts_dir", "pipeline/scroll/parts")
YOUTUBE_DESCRIPTIONS_DIR = _cfg_path("youtube", "descriptions_dir", "pipeline/descriptions")
YOUTUBE_UPLOAD_STATE_FILE = _cfg_path("youtube", "upload_state_file", "pipeline/descriptions/upload_state.json")

YOUTUBE_PLAYLIST_ID = _cfg_text("youtube", "playlist_id", "")
YOUTUBE_PLAYLIST_TITLE = _cfg_text("youtube", "playlist_title", "")
YOUTUBE_PLAYLIST_PRIVACY = _cfg_text("youtube", "playlist_privacy", "public")
YOUTUBE_THUMBNAIL_FILE = _cfg_text("youtube", "thumbnail_file", "")

YOUTUBE_PUBLISH_START_DATE = _cfg_text("youtube", "publish_start_date", "")
YOUTUBE_PUBLISH_TIME = _cfg_text("youtube", "publish_time", "10:00")
YOUTUBE_PUBLISH_TIMEZONE = _cfg_text("youtube", "publish_timezone", "UTC")
YOUTUBE_PUBLISH_INTERVAL_DAYS = float(_cfg_text("youtube", "publish_interval_days", "1"))
YOUTUBE_PUBLISH_PRIVACY_STATUS = _cfg_text("youtube", "publish_privacy_status", "public")

YOUTUBE_UPLOAD_DELAY_SECONDS = float(_cfg_text("youtube", "upload_delay_seconds", "300"))
YOUTUBE_UPLOAD_CHUNK_SIZE = int(_cfg_text("youtube", "upload_chunk_size", "5242880"))
YOUTUBE_UPLOAD_MAX_RETRIES = _cfg_int("youtube", "upload_max_retries", 5)
YOUTUBE_CATEGORY_ID = _cfg_text("youtube", "category_id", "27")
YOUTUBE_DEFAULT_LANGUAGE = _cfg_text("youtube", "default_language", "en")
YOUTUBE_MADE_FOR_KIDS = _cfg_text("youtube", "made_for_kids", "false").lower() == "true"
YOUTUBE_DECLARE_ALTERED_CONTENT = _cfg_text("youtube", "declare_altered_content", "false").lower() == "true"
YOUTUBE_TITLE_SOURCE = _cfg_text("youtube", "title_source", "filename").lower()
