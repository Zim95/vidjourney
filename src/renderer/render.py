"""
Renderer orchestrator.

Watches pipeline/render/ for new .render.json files.
For each: renders with Manim to produce silent .mp4.
"""
import os
import subprocess
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from src.utils import logger, timer
from src.config.constants import GROUPING_RENDER_DIR, GROUPING_MANIM_VIDEO_DIR
from concurrent.futures import ThreadPoolExecutor


RENDER_DIR = GROUPING_RENDER_DIR
MANIM_VIDEO_DIR = GROUPING_MANIM_VIDEO_DIR


@timer(label="Render manim scene")
def render_manim(render_file: Path, scene_name: str) -> Path:
    """Render .render.json with manim to produce silent .mp4"""
    logger.info(f"Rendering with Manim: {render_file.name} → {scene_name}.mp4")
    env = os.environ.copy()
    env["RENDERER_INSTRUCTIONS_FILE"] = str(render_file)

    subprocess.run(
        [
            "python", "-m", "manim", "-ql",
            "src/renderer/manim/manim_runner.py", "ManimScene",
            "-o", scene_name,
        ],
        check=True,
        env=env,
        capture_output=True,
    )

    video_path = MANIM_VIDEO_DIR / f"{scene_name}.mp4"
    if video_path.exists():
        logger.info(f"Manim output: {video_path}")
        return video_path

    for p in Path("media").rglob(f"{scene_name}.mp4"):
        logger.info(f"Manim output: {p}")
        return p

    raise FileNotFoundError(f"Manim output not found for {scene_name}")


# --- Watchdog ---

class RenderHandler(FileSystemEventHandler):
    def __init__(self, executor):
        self._executor = executor

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = Path(event.src_path)
        if filepath.suffix == ".json" and filepath.stem.endswith(".render"):
            scene_name = filepath.stem.removesuffix(".render")
            video_path = MANIM_VIDEO_DIR / f"{scene_name}.mp4"
            if not video_path.exists():
                logger.info(f"[watchdog] New render file detected: {filepath.name}")
                self._executor.submit(self._process, filepath, scene_name)

    def _process(self, filepath: Path, scene_name: str):
        try:
            render_manim(filepath, scene_name)
        except Exception as e:
            logger.error(f"Render failed: {scene_name} — {e}")


def start_watcher(executor=None) -> Observer:
    RENDER_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()
    handler = RenderHandler(executor=_executor)
    observer = Observer()
    observer.schedule(handler, str(RENDER_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer: Observer) -> None:
    observer.stop()
    observer.join()


# --- Batch processing ---

@timer(label="Render all scenes")
def render_all() -> None:
    """Render all pending .render.json files."""
    logger.info("Rendering all pending scenes...")
    render_files = sorted(RENDER_DIR.glob("*.render.json"))
    rendered = 0
    for rf in render_files:
        scene_name = rf.stem.removesuffix(".render")
        video_path = MANIM_VIDEO_DIR / f"{scene_name}.mp4"
        if video_path.exists():
            continue
        try:
            render_manim(rf, scene_name)
            rendered += 1
        except Exception as e:
            print(f"  FAILED: {scene_name} — {e}")
    print(f"Rendered {rendered} video(s).")


if __name__ == "__main__":
    # Usage:
    #   python -m src.renderer.render pipeline/render/timeline_section_2_scene_3.render.json
    #   python -m src.renderer.render --all
    #   python -m src.renderer.render --watch
    import sys
    import time
    import argparse

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="Manim renderer")
    parser.add_argument("render_file", type=str, nargs="?", help="Path to a .render.json file")
    parser.add_argument("--all", action="store_true", help="Render all pending files")
    parser.add_argument("--watch", action="store_true", help="Watch render dir for new files")
    args = parser.parse_args()

    _executor = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)
    logger.info(f"Standalone executor: {PIPELINE_THREAD_WORKERS} thread workers")

    if args.watch:
        logger.info(f"Watching {RENDER_DIR} for new render files...")
        observer = start_watcher(executor=_executor)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_watcher(observer)
            _executor.shutdown(wait=True)
            logger.info("Stopped.")
    elif args.all:
        render_all()
        _executor.shutdown(wait=True)
    elif args.render_file:
        path = Path(args.render_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        scene_name = path.stem.removesuffix(".render")
        render_manim(path, scene_name)
        _executor.shutdown(wait=True)
    else:
        parser.print_help()
