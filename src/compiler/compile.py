"""
Compiler orchestrator.

Watches pipeline/groups/timelines/ for new timeline files.
For each timeline: compiles to .scene DSL → .render.json.
"""
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor

from src.utils import logger, timer
from src.config.constants import GROUPING_RENDER_DIR
from src.compiler.dsl_compiler import (
    compile_timeline,
    process_all_timelines,
    TIMELINES_DIR,
    SCENE_FILES_DIR,
)
from src.dsl.parser import parse_scene
from src.dsl.transformer import SceneModelTransformer


RENDER_DIR = GROUPING_RENDER_DIR


@timer(label="Compile timeline")
def compile_to_render_json(timeline_file: Path) -> Path:
    """Full compile: timeline → .scene → .render.json"""
    SCENE_FILES_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)

    scene_file = SCENE_FILES_DIR / f"{timeline_file.stem}.scene"
    render_file = RENDER_DIR / f"{timeline_file.stem}.render.json"

    if not scene_file.exists():
        logger.info(f"Compiling DSL: {timeline_file.name} → {scene_file.name}")
        dsl = compile_timeline(timeline_file)
        scene_file.write_text(dsl, encoding="utf-8")

    if not render_file.exists():
        logger.info(f"Generating render JSON: {scene_file.name} → {render_file.name}")
        ast = parse_scene(
            scene_path=scene_file,
            grammar_path=Path("src/dsl/renderer_dsl.lark"),
        )
        model = SceneModelTransformer().transform(ast)
        model.write_json(render_file)

    return render_file


# --- Watchdog ---

class TimelineHandler(FileSystemEventHandler):
    def __init__(self, executor):
        self._executor = executor

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = Path(event.src_path)
        if filepath.suffix == ".txt" and filepath.stem.startswith("timeline_"):
            logger.info(f"[watchdog] New timeline detected: {filepath.name}")
            self._executor.submit(self._process, filepath)

    def _process(self, filepath: Path):
        try:
            compile_to_render_json(filepath)
        except Exception as e:
            logger.error(f"Compile failed: {filepath.name} — {e}")


def start_watcher(executor=None) -> Observer:
    from concurrent.futures import ProcessPoolExecutor as _DefaultPool
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)
    SCENE_FILES_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()
    handler = TimelineHandler(executor=_executor)
    observer = Observer()
    observer.schedule(handler, str(TIMELINES_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer: Observer) -> None:
    observer.stop()
    observer.join()


# --- Batch processing ---

@timer(label="Compile all timelines")
def compile_all() -> None:
    """Compile all pending timeline files to .scene and .render.json."""
    logger.info("Compiling all pending timelines...")
    process_all_timelines()

    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    compiled = 0
    for scene_file in sorted(SCENE_FILES_DIR.glob("*.scene")):
        render_file = RENDER_DIR / f"{scene_file.stem}.render.json"
        if render_file.exists():
            continue
        try:
            ast = parse_scene(
                scene_path=scene_file,
                grammar_path=Path("src/dsl/renderer_dsl.lark"),
            )
            model = SceneModelTransformer().transform(ast)
            model.write_json(render_file)
            compiled += 1
        except Exception as e:
            print(f"  FAILED: {scene_file.name} — {e}")

    print(f"Compiled {compiled} render file(s).")


if __name__ == "__main__":
    # Usage:
    #   python -m src.compiler.compile pipeline/groups/timelines/timeline_section_2_scene_3.txt
    #   python -m src.compiler.compile --all
    #   python -m src.compiler.compile --watch
    import sys
    import time
    import argparse
    from concurrent.futures import ProcessPoolExecutor

    from src.config.constants import PIPELINE_PROCESS_WORKERS

    parser = argparse.ArgumentParser(description="DSL compiler pipeline")
    parser.add_argument("timeline_file", type=str, nargs="?", help="Path to a timeline file")
    parser.add_argument("--all", action="store_true", help="Compile all pending timelines")
    parser.add_argument("--watch", action="store_true", help="Watch timelines dir for new files")
    args = parser.parse_args()

    _executor = ProcessPoolExecutor(max_workers=PIPELINE_PROCESS_WORKERS)
    logger.info(f"Standalone executor: {PIPELINE_PROCESS_WORKERS} process workers")

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
    elif args.all:
        compile_all()
        _executor.shutdown(wait=True)
    elif args.timeline_file:
        path = Path(args.timeline_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        compile_to_render_json(path)
        _executor.shutdown(wait=True)
    else:
        parser.print_help()
