"""
Narration orchestrator.

Watches pipeline/groups/timelines/ for new timeline files.
For each timeline: generates TTS narration audio.
"""
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor

from src.narration.narrator import generate_narration
from src.utils import logger, timer
from src.config.constants import GROUPING_TIMELINES_DIR


TIMELINES_DIR = GROUPING_TIMELINES_DIR


@timer(label="Narrate scene")
def narrate_scene(timeline_file: Path) -> Path:
    """Generate narration audio for a scene."""
    scene_name = timeline_file.stem
    logger.info(f"Narrating {scene_name}...")
    audio_path = generate_narration(timeline_file)
    logger.info(f"Audio ready: {audio_path.name}")
    return audio_path


# --- Watchdog ---

class TimelineNarrationHandler(FileSystemEventHandler):
    def __init__(self, executor):
        self._executor = executor

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = Path(event.src_path)
        if filepath.suffix == ".txt" and filepath.stem.startswith("timeline_"):
            logger.info(f"[watchdog] New timeline for narration: {filepath.name}")
            self._executor.submit(self._process, filepath)

    def _process(self, filepath: Path):
        try:
            narrate_scene(filepath)
        except Exception as e:
            logger.error(f"Narrate failed: {filepath.name} — {e}")


def start_watcher(executor=None, observer: Observer | None = None) -> Observer:
    """Register a TimelineNarrationHandler on `pipeline/groups/timelines/`.

    If `observer` is provided, just attaches this handler to it. Useful when
    sharing one Observer between compile and narrate (both watch the same
    path — fsevents errors if two Observer instances watch one path).
    """
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()
    handler = TimelineNarrationHandler(executor=_executor)

    if observer is None:
        observer = Observer()
        observer.schedule(handler, str(TIMELINES_DIR), recursive=False)
        observer.start()
    else:
        observer.schedule(handler, str(TIMELINES_DIR), recursive=False)
    return observer


def stop_watcher(observer: Observer) -> None:
    observer.stop()
    observer.join()


# --- Batch processing ---

def narrate_section(section_name: str) -> list[Path]:
    """Generate narration for all scenes in a section."""
    timeline_files = sorted(TIMELINES_DIR.glob(f"timeline_{section_name}_scene_*.txt"))
    if not timeline_files:
        print(f"No timeline files found for {section_name}")
        return []

    results = []
    for tf in timeline_files:
        try:
            result = narrate_scene(tf)
            results.append(result)
        except Exception as e:
            print(f"  FAILED: {tf.name} — {e}")

    return results


if __name__ == "__main__":
    # Usage:
    #   python -m src.narration.narrate pipeline/groups/timelines/timeline_section_2_scene_3.txt
    #   python -m src.narration.narrate section_2
    #   python -m src.narration.narrate --watch
    import sys
    import time
    import argparse

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="Generate TTS narration")
    parser.add_argument("target", type=str, nargs="?", help="Timeline file path or section name")
    parser.add_argument("--watch", action="store_true", help="Watch timelines dir for new files")
    args = parser.parse_args()

    _executor = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)
    logger.info(f"Standalone executor: {PIPELINE_THREAD_WORKERS} thread workers")

    if args.watch:
        logger.info(f"Watching {TIMELINES_DIR} for new timelines...")
        observer = start_watcher(executor=_executor)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_watcher(observer)
            _executor.shutdown(wait=True)
            logger.info("Stopped.")
    elif args.target:
        target = args.target
        if target.endswith(".txt") and Path(target).exists():
            narrate_scene(Path(target))
        else:
            narrate_section(target)
        _executor.shutdown(wait=True)
    else:
        parser.print_help()
