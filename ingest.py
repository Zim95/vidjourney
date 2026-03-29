from src.ingestion.ingest_pdf import ingest
from src.scene_grouping import group
from src.compiler import compile
from src.pipeline import run_pipeline
from pathlib import Path


def main() -> None:
	ingest(Path('/Users/namahshrestha/Downloads/Books/System Design/Designing Data Intensive Applications.pdf'))
	group()
	compile()
	run_pipeline(renderer="manim")


if __name__ == "__main__":
	main()