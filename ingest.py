from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from ingestion import (
    build_semantic_document,
    clean_document,
    enrich_with_structural_metadata,
    read_pdf,
    split_into_chapters,
    validate_semantic_document,
)
from story_planner import build_scene_plan, build_story_plan_ai


PDF_SOURCE_PATH = Path("/Users/namahshrestha/Downloads/Books/System Design/Designing Data Intensive Applications.pdf")
INGESTION_OUTPUT_PATH = Path("input/ingestion_structure.json")
STORY_PLAN_OUTPUT_PATH = Path("input/story_plan.json")
STORY_PLAN_AI_OUTPUT_PATH = Path("input/story_plan_ai.json")
INGEST_LOG_LEVEL = "INFO"


def _configure_logging() -> None:
    level = getattr(logging, INGEST_LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )


def build_ingestion_structure(pdf_path: Path) -> tuple[dict, dict, dict]:
    pdf_document = read_pdf(pdf_path)
    cleaned_document = clean_document(pdf_document)
    chaptered_document = split_into_chapters(cleaned_document)
    semantic_document = build_semantic_document(chaptered_document)
    semantic_validation = validate_semantic_document(semantic_document)
    metadata_enriched = enrich_with_structural_metadata(semantic_document)
    story_plan = build_scene_plan(metadata_enriched)
    story_plan_ai = build_story_plan_ai(story_plan)

    structure = {
        "source_pdf": str(pdf_path),
        "page_count": len(pdf_document.pages),
        "cleaned_document": asdict(cleaned_document),
        "chapters": asdict(chaptered_document),
        "semantic_document": semantic_document.to_dict(),
        "semantic_validation": semantic_validation.to_dict(),
        "metadata_enriched_document": metadata_enriched.to_dict(),
    }
    return structure, story_plan.to_dict(), story_plan_ai.to_dict()


def write_structure(output_path: Path, structure: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(structure, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    _configure_logging()

    if not PDF_SOURCE_PATH.exists():
        raise FileNotFoundError(
            f"PDF file not found at {PDF_SOURCE_PATH}. "
            "Update PDF_SOURCE_PATH in ingest.py to your current PDF location."
        )

    structure, story_plan, story_plan_ai = build_ingestion_structure(PDF_SOURCE_PATH)
    write_structure(INGESTION_OUTPUT_PATH, structure)
    write_structure(STORY_PLAN_OUTPUT_PATH, story_plan)
    write_structure(STORY_PLAN_AI_OUTPUT_PATH, story_plan_ai)
    print(f"Ingestion complete: {INGESTION_OUTPUT_PATH}")
    print(f"Story plan complete: {STORY_PLAN_OUTPUT_PATH}")
    print(f"Story AI plan complete: {STORY_PLAN_AI_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
