from src.ingestion.ingest_pdf import ingest
from pathlib import Path


def main() -> None:
	ingest(Path('/Users/namahshrestha/Downloads/Books/System Design/Designing Data Intensive Applications.pdf'))


if __name__ == "__main__":
	main()