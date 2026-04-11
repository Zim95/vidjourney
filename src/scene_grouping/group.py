"""
Scene grouping orchestrator.

Watches pipeline/sections/ for new files.
For each section: generates storyboard (LLM) → timelines (deterministic).

Also watches storyboard dir → generates timelines via watchdog.
"""
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor

from src.utils import logger, timer
from src.scene_grouping.llm_storyboard import (
    BACKENDS,
    storyboard_section_file,
)
from src.scene_grouping import timeline
from src.config.constants import (
    GROUPING_SECTIONS_DIR,
    GROUPING_STORYBOARD_DIR,
)


SECTIONS_DIR = GROUPING_SECTIONS_DIR
STORYBOARD_DIR = GROUPING_STORYBOARD_DIR


def _collect_pending_sections() -> list[Path]:
    section_files = sorted(SECTIONS_DIR.glob("section_*.txt"))
    return [f for f in section_files if not (STORYBOARD_DIR / f.name).exists()]


@timer(label="Group section")
def group_section(section_file: Path, backend: str = "ollama") -> Path:
    """Run storyboard + timeline for a single section."""
    STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(f"Generating storyboard for {section_file.name} using {backend}")
    result = storyboard_section_file(section_file, backend=backend)
    out = STORYBOARD_DIR / section_file.name
    out.write_text(result, encoding="utf-8")
    logger.info(f"Storyboard written: {out.name}")

    logger.info(f"Generating timelines for {out.name}")
    timeline.process_storyboard_file(out)
    return out


# --- Watchdog ---

class SectionHandler(FileSystemEventHandler):
    def __init__(self, backend: str, executor: ThreadPoolExecutor):
        self._executor = executor
        self._backend = backend

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = Path(event.src_path)
        if filepath.suffix == ".txt" and filepath.stem.startswith("section_"):
            if not (STORYBOARD_DIR / filepath.name).exists():
                logger.info(f"[watchdog] New section detected: {filepath.name}")
                self._executor.submit(group_section, filepath, self._backend)


def start_watcher(backend: str = "ollama", executor: ThreadPoolExecutor | None = None) -> tuple:
    """Start watching sections dir + storyboard dir. Returns (section_observer, timeline_observer)."""
    SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()
    section_handler = SectionHandler(backend=backend, executor=_executor)
    section_observer = Observer()
    section_observer.schedule(section_handler, str(SECTIONS_DIR), recursive=False)
    section_observer.start()

    timeline_observer = timeline.start_watcher(executor=_executor)

    return (section_observer, timeline_observer)


def stop_watcher(observers: tuple) -> None:
    section_observer, timeline_observer = observers
    section_observer.stop()
    section_observer.join()
    timeline.stop_watcher(timeline_observer)


# --- Batch processing ---

@timer(label="Group all sections")
def group_all(backend: str = "ollama") -> None:
    STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)

    pending = _collect_pending_sections()
    if not pending:
        logger.info("All sections already have storyboards. Nothing to do.")
        return

    logger.info(f"Found {len(pending)} pending sections.")

    timeline_observer = timeline.start_watcher()
    print("Timeline watcher started.")

    try:
        for i, section_file in enumerate(pending, 1):
            print(f"[{i}/{len(pending)}] {section_file.name}...")
            try:
                result = storyboard_section_file(section_file, backend=backend)
                out = STORYBOARD_DIR / section_file.name
                out.write_text(result, encoding="utf-8")
                print(f"  STORYBOARD: {out.name}")
            except Exception as e:
                print(f"  FAILED: {section_file.name} — {e}")
    finally:
        import time
        time.sleep(2)
        timeline.stop_watcher(timeline_observer)
        print("Timeline watcher stopped.")

    print("Done.")


if __name__ == "__main__":
    # Usage:
    #   python -m src.scene_grouping.group pipeline/sections/section_2.txt --backend ollama
    #   python -m src.scene_grouping.group --all --backend ollama
    #   python -m src.scene_grouping.group --watch --backend ollama
    import sys
    import time
    import argparse

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="Scene grouping pipeline")
    parser.add_argument("section_file", type=str, nargs="?", help="Path to a section file")
    parser.add_argument("--backend", type=str, default="ollama", choices=list(BACKENDS.keys()))
    parser.add_argument("--all", action="store_true", help="Process all pending sections")
    parser.add_argument("--watch", action="store_true", help="Watch sections dir for new files")
    args = parser.parse_args()

    _executor = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)
    logger.info(f"Standalone executor: {PIPELINE_THREAD_WORKERS} thread workers")

    if args.watch:
        logger.info(f"Watching {SECTIONS_DIR} for new sections...")
        observers = start_watcher(backend=args.backend, executor=_executor)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_watcher(observers)
            _executor.shutdown(wait=True)
            logger.info("Stopped.")
    elif args.all:
        group_all(backend=args.backend)
        _executor.shutdown(wait=True)
    elif args.section_file:
        path = Path(args.section_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        group_section(path, backend=args.backend)
        _executor.shutdown(wait=True)
    else:
        parser.print_help()
