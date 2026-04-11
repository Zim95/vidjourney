"""
Assembler orchestrator.

Watches for completed narration (.wav) and rendered video (.mp4) pairs.
When both exist for a scene, merges them into the final output.
"""
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor

from src.assembler.ffmpeg_merge import merge_audio_video
from src.utils import logger, timer
from src.config.constants import (
    GROUPING_NARRATION_DIR,
    GROUPING_OUTPUT_DIR,
    GROUPING_MANIM_VIDEO_DIR,
)


NARRATION_DIR = GROUPING_NARRATION_DIR
OUTPUT_DIR = GROUPING_OUTPUT_DIR
MANIM_VIDEO_DIR = GROUPING_MANIM_VIDEO_DIR


def _find_video(scene_name: str) -> Path | None:
    """Find the manim-rendered video for a scene."""
    video_path = MANIM_VIDEO_DIR / f"{scene_name}.mp4"
    if video_path.exists():
        return video_path
    for p in Path("media").rglob(f"{scene_name}.mp4"):
        return p
    return None


@timer(label="Assemble scene")
def assemble_scene(scene_name: str) -> Path | None:
    """Merge audio + video for a single scene if both exist."""
    audio_path = NARRATION_DIR / f"{scene_name}.wav"
    video_path = _find_video(scene_name)

    if not audio_path.exists():
        logger.info(f"Waiting for audio: {scene_name}")
        return None
    if not video_path:
        logger.info(f"Waiting for video: {scene_name}")
        return None

    output_file = OUTPUT_DIR / f"{scene_name}.mp4"
    if output_file.exists():
        logger.info(f"Already assembled: {output_file.name}")
        return output_file

    logger.info(f"Assembling: {audio_path.name} + {video_path.name} → {scene_name}.mp4")
    return merge_audio_video(video_path, audio_path, scene_name)


# --- Watchdog ---

class NarrationHandler(FileSystemEventHandler):
    """Watches narration dir. When a .wav appears, tries to merge with video."""

    def __init__(self, executor):
        self._executor = executor

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = Path(event.src_path)
        if filepath.suffix == ".wav":
            scene_name = filepath.stem
            logger.info(f"[watchdog] New narration detected: {filepath.name}")
            self._executor.submit(self._try_assemble, scene_name)

    def _try_assemble(self, scene_name: str):
        try:
            assemble_scene(scene_name)
        except Exception as e:
            logger.error(f"Assemble failed: {scene_name} — {e}")


def start_watcher(executor=None) -> Observer:
    NARRATION_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()
    handler = NarrationHandler(executor=_executor)
    observer = Observer()
    observer.schedule(handler, str(NARRATION_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer: Observer) -> None:
    observer.stop()
    observer.join()


# --- Batch processing ---

def assemble_section(section_name: str) -> list[Path]:
    """Assemble all scenes for a section."""
    wav_files = sorted(NARRATION_DIR.glob(f"timeline_{section_name}_scene_*.wav"))
    if not wav_files:
        print(f"No narration files found for {section_name}")
        return []

    outputs = []
    for wav in wav_files:
        scene_name = wav.stem
        try:
            output = assemble_scene(scene_name)
            if output:
                outputs.append(output)
        except Exception as e:
            print(f"  FAILED: {scene_name} — {e}")

    return outputs


if __name__ == "__main__":
    # Usage:
    #   python -m src.assembler.assemble timeline_section_2_scene_3
    #   python -m src.assembler.assemble section_2
    #   python -m src.assembler.assemble --watch
    import sys
    import time
    import argparse
    from concurrent.futures import ThreadPoolExecutor

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="Assemble narration + video")
    parser.add_argument("target", type=str, nargs="?", help="Scene name or section name")
    parser.add_argument("--watch", action="store_true", help="Watch narration dir for new files")
    args = parser.parse_args()

    _executor = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)
    logger.info(f"Standalone executor: {PIPELINE_THREAD_WORKERS} thread workers")

    if args.watch:
        logger.info(f"Watching {NARRATION_DIR} for new narration files...")
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
        if target.startswith("timeline_"):
            assemble_scene(target)
        else:
            assemble_section(target)
        _executor.shutdown(wait=True)
    else:
        parser.print_help()
