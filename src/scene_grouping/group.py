"""
Scene grouping orchestrator.

Watches pipeline/sections/ for new files.
For each section: groups elements (LLM) → persists content groups.

Output:
  - pipeline/groups/content_groups/section_N.txt

Usage:
    python -m src.scene_grouping.group pipeline/sections/section_3.txt
    python -m src.scene_grouping.group --all
    python -m src.scene_grouping.group --watch
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from src.utils import logger, timer
from src.scene_grouping.llm_grouper import (
    group_section_file,
    serialize_groups,
)
from src.config.constants import (
    GROUPING_SECTIONS_DIR,
    GROUPING_CONTENT_GROUPS_DIR,
)


SECTIONS_DIR = GROUPING_SECTIONS_DIR
CONTENT_GROUPS_DIR = GROUPING_CONTENT_GROUPS_DIR


def _collect_pending_sections() -> list[Path]:
    section_files = sorted(SECTIONS_DIR.glob("section_*.txt"))
    return [f for f in section_files if not (CONTENT_GROUPS_DIR / f.name).exists()]


@timer(label="Group section")
def group_section(section_file: Path) -> Path:
    """Group elements for a single section and persist to disk."""
    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)

    groups_file = CONTENT_GROUPS_DIR / section_file.name

    if groups_file.exists():
        logger.info(f"Already grouped: {groups_file.name}")
        return groups_file

    logger.info(f"Grouping elements for {section_file.name}")
    groups = group_section_file(section_file)
    groups_file.write_text(serialize_groups(groups), encoding="utf-8")
    logger.info(f"Wrote content groups: {groups_file.name} ({len(groups)} groups)")
    return groups_file


# --- Watchdog ---

class SectionHandler(FileSystemEventHandler):
    def __init__(self, executor: ThreadPoolExecutor):
        self._executor = executor

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = Path(event.src_path)
        if filepath.suffix == ".txt" and filepath.stem.startswith("section_"):
            if not (CONTENT_GROUPS_DIR / filepath.name).exists():
                logger.info(f"[watchdog] New section detected: {filepath.name}")
                self._executor.submit(self._process, filepath)

    def _process(self, filepath: Path):
        try:
            group_section(filepath)
        except Exception as e:
            logger.error(f"Grouping failed: {filepath.name} — {e}")


def start_watcher(executor=None) -> Observer:
    SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()
    handler = SectionHandler(executor=_executor)
    observer = Observer()
    observer.schedule(handler, str(SECTIONS_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer: Observer) -> None:
    observer.stop()
    observer.join()


# --- Batch processing ---

@timer(label="Group all sections")
def group_all(executor: ThreadPoolExecutor) -> None:
    """Process all pending sections concurrently using the shared executor."""
    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)

    pending = _collect_pending_sections()
    if not pending:
        logger.info("All sections already grouped. Nothing to do.")
        return

    logger.info(f"Found {len(pending)} pending sections. Processing concurrently...")

    futures = {
        executor.submit(group_section, section_file): section_file
        for section_file in pending
    }

    succeeded = 0
    failed = 0
    for future in as_completed(futures):
        section_file = futures[future]
        try:
            future.result()
            succeeded += 1
            logger.info(f"[{succeeded + failed}/{len(pending)}] Done: {section_file.name}")
        except Exception as e:
            failed += 1
            logger.error(f"[{succeeded + failed}/{len(pending)}] FAILED: {section_file.name} — {e}")

    logger.info(f"Completed: {succeeded} succeeded, {failed} failed out of {len(pending)}")


if __name__ == "__main__":
    import sys
    import time
    import argparse

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="Scene grouping pipeline")
    parser.add_argument("section_file", type=str, nargs="?", help="Path to a section file")
    parser.add_argument("--all", action="store_true", help="Process all pending sections")
    parser.add_argument("--watch", action="store_true", help="Watch sections dir for new files")
    args = parser.parse_args()

    _executor = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)
    logger.info(f"Shared executor: {PIPELINE_THREAD_WORKERS} thread workers")

    if args.watch:
        logger.info(f"Watching {SECTIONS_DIR} for new sections...")
        observer = start_watcher(executor=_executor)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_watcher(observer)
            _executor.shutdown(wait=True)
            logger.info("Stopped.")
    elif args.all:
        group_all(executor=_executor)
        _executor.shutdown(wait=True)
    elif args.section_file:
        path = Path(args.section_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        group_section(path)
        _executor.shutdown(wait=True)
    else:
        parser.print_help()
