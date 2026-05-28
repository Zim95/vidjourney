"""
Icon downloader orchestrator.

Scans storyboard files for entities (box labels, list items, highlight targets)
and downloads icons for each unique entity. Runs as a watcher on the storyboard
directory.
"""
import re
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor

from src.utils import logger, timer
from src.icons.icon_downloader import download_icon
from src.config.constants import GROUPING_STORYBOARD_DIR, ICONS_DIR


STORYBOARD_DIR = GROUPING_STORYBOARD_DIR


def _extract_entities_from_storyboard(content: str) -> set[str]:
    """Extract all entity names (box labels, list items, highlight targets) from a storyboard."""
    entities: set[str] = set()

    # box labels
    for m in re.finditer(r'type:\s*box\s*\n\s*label:\s*"([^"]+)"', content):
        entities.add(m.group(1).strip())

    # list items: items: ["a", "b", "c"] or multi-line items
    for m in re.finditer(r'items:\s*\[(.+?)\]', content, re.DOTALL):
        inner = m.group(1)
        for item_match in re.finditer(r'"([^"]+)"', inner):
            entities.add(item_match.group(1).strip())

    # highlight targets
    for m in re.finditer(r'type:\s*highlight\s*\n\s*target:\s*"([^"]+)"', content):
        entities.add(m.group(1).strip())

    # arrow from/to
    for m in re.finditer(r'(?:from|to):\s*"([^"]+)"', content):
        entities.add(m.group(1).strip())

    # strip bullet markers and extract core noun for list items like "• Store data (databases)"
    cleaned: set[str] = set()
    for e in entities:
        e = re.sub(r"^[•·\-*]\s*", "", e).strip()
        if not e:
            continue
        cleaned.add(e)

    return cleaned


@timer(label="Download icons for storyboard")
def download_icons_for_storyboard(filepath: Path) -> dict[str, Path | None]:
    """Extract entities from a storyboard and download icons for each."""
    content = filepath.read_text(encoding="utf-8", errors="replace")
    entities = _extract_entities_from_storyboard(content)

    if not entities:
        logger.info(f"No entities found in {filepath.name}")
        return {}

    logger.info(f"Found {len(entities)} unique entities in {filepath.name}")
    results: dict[str, Path | None] = {}
    for entity in sorted(entities):
        results[entity] = download_icon(entity)

    downloaded = sum(1 for p in results.values() if p is not None)
    logger.info(f"Downloaded {downloaded}/{len(entities)} icons for {filepath.name}")
    return results


def download_all_storyboards() -> None:
    """Download icons for all storyboards in the directory."""
    STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(STORYBOARD_DIR.glob("section_*.txt")):
        download_icons_for_storyboard(f)


# --- Watchdog ---

class StoryboardIconHandler(FileSystemEventHandler):
    def __init__(self, executor):
        self._executor = executor

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = Path(event.src_path)
        if filepath.suffix == ".txt" and filepath.stem.startswith("section_"):
            logger.info(f"[watchdog] New storyboard for icon download: {filepath.name}")
            self._executor.submit(self._process, filepath)

    def _process(self, filepath: Path):
        try:
            download_icons_for_storyboard(filepath)
        except Exception as e:
            logger.error(f"Icon download failed for {filepath.name}: {e}")


def start_watcher(executor=None) -> Observer:
    STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()
    handler = StoryboardIconHandler(executor=_executor)
    observer = Observer()
    observer.schedule(handler, str(STORYBOARD_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer: Observer) -> None:
    observer.stop()
    observer.join()


if __name__ == "__main__":
    # Usage:
    #   python -m src.icons.download pipeline/groups/storyboard/section_2.txt
    #   python -m src.icons.download --all
    #   python -m src.icons.download --watch
    import sys
    import time
    import argparse

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="Icon downloader")
    parser.add_argument("storyboard_file", type=str, nargs="?", help="Path to a storyboard file")
    parser.add_argument("--all", action="store_true", help="Download icons for all storyboards")
    parser.add_argument("--watch", action="store_true", help="Watch storyboard dir for new files")
    args = parser.parse_args()

    _executor = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)
    logger.info(f"Standalone executor: {PIPELINE_THREAD_WORKERS} thread workers")

    if args.watch:
        logger.info(f"Watching {STORYBOARD_DIR} for new storyboards...")
        observer = start_watcher(executor=_executor)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_watcher(observer)
            _executor.shutdown(wait=True)
            logger.info("Stopped.")
    elif args.all:
        download_all_storyboards()
        _executor.shutdown(wait=True)
    elif args.storyboard_file:
        path = Path(args.storyboard_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        download_icons_for_storyboard(path)
        _executor.shutdown(wait=True)
    else:
        parser.print_help()
