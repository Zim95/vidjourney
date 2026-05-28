"""
Assembler orchestrator.

Watches for completed narration (.wav) and rendered video (.mp4) pairs.
When both exist for a scene, merges them into the final output.
"""
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor

from src.assembler.ffmpeg_merge import merge_audio_video, concat_videos
from src.subtitles.srt_writer import generate_srt
from src.utils import logger, timer
from src.config.constants import (
    GROUPING_NARRATION_DIR,
    GROUPING_OUTPUT_DIR,
    GROUPING_MANIM_VIDEO_DIR,
    GROUPING_TIMELINES_DIR,
    SUBTITLES_DIR as _SUBTITLES_DIR,
)


NARRATION_DIR = GROUPING_NARRATION_DIR
OUTPUT_DIR = GROUPING_OUTPUT_DIR
MANIM_VIDEO_DIR = GROUPING_MANIM_VIDEO_DIR
TIMELINES_DIR = GROUPING_TIMELINES_DIR
SUBTITLES_DIR = _SUBTITLES_DIR


def _find_video(scene_name: str) -> Path | None:
    """Find the manim-rendered video for a scene."""
    video_path = MANIM_VIDEO_DIR / f"{scene_name}.mp4"
    if video_path.exists():
        return video_path
    for p in Path("media").rglob(f"{scene_name}.mp4"):
        return p
    return None


def _ensure_subtitles(scene_name: str) -> Path | None:
    """Generate the SRT for a scene from its timeline file. Returns None if no timeline."""
    timeline_path = TIMELINES_DIR / f"{scene_name}.txt"
    if not timeline_path.exists():
        return None
    SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)
    srt_path = SUBTITLES_DIR / f"{scene_name}.srt"
    if not srt_path.exists():
        generate_srt(timeline_path, srt_path)
    return srt_path if srt_path.exists() and srt_path.stat().st_size > 0 else None


def _is_assembled_output_stale(
    output_file: Path,
    video_path: Path,
    audio_path: Path,
    srt_path: Path | None,
) -> bool:
    """Return True when any input is newer than the assembled output.

    Without this check, re-rendering manim (or regenerating narration/srt)
    silently produces stale section mp4s because `assemble_scene` short-
    circuits on `output_file.exists()`. That bit us once with entity-pill
    quote scenes that lingered after the listify/quote refactor.
    """
    output_mtime = output_file.stat().st_mtime
    input_mtimes = [video_path.stat().st_mtime, audio_path.stat().st_mtime]
    if srt_path is not None and srt_path.exists():
        input_mtimes.append(srt_path.stat().st_mtime)
    return max(input_mtimes) > output_mtime


@timer(label="Assemble scene")
def assemble_scene(scene_name: str) -> Path | None:
    """Merge audio + video for a single scene (with burned-in subtitles) if both exist."""
    audio_path = NARRATION_DIR / f"{scene_name}.wav"
    video_path = _find_video(scene_name)

    if not audio_path.exists():
        logger.info(f"Waiting for audio: {scene_name}")
        return None
    if not video_path:
        logger.info(f"Waiting for video: {scene_name}")
        return None

    output_file = OUTPUT_DIR / f"{scene_name}.mp4"
    srt_path = _ensure_subtitles(scene_name)
    if output_file.exists() and not _is_assembled_output_stale(
        output_file, video_path, audio_path, srt_path,
    ):
        logger.info(f"Already assembled: {output_file.name}")
        return output_file
    if output_file.exists():
        logger.info(f"Re-assembling (inputs newer): {output_file.name}")

    logger.info(f"Assembling: {audio_path.name} + {video_path.name} → {scene_name}.mp4")
    return merge_audio_video(video_path, audio_path, scene_name, subtitles_path=srt_path)


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

def _scene_sort_key(scene_name: str) -> int:
    """Extract scene number from 'timeline_section_N_scene_M' for ordering."""
    import re
    m = re.search(r"_scene_(\d+)$", scene_name)
    return int(m.group(1)) if m else 0


def assemble_section(section_name: str) -> list[Path]:
    """Assemble all scenes for a section into per-scene narrated videos."""
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


@timer(label="Concat section")
def concat_section(section_name: str) -> Path | None:
    """Concatenate all per-scene videos for a section into a single video."""
    scene_videos = sorted(
        OUTPUT_DIR.glob(f"timeline_{section_name}_scene_*.mp4"),
        key=lambda p: _scene_sort_key(p.stem),
    )
    if not scene_videos:
        logger.warning(f"No scene videos found for {section_name}")
        return None

    output_file = OUTPUT_DIR / f"{section_name}.mp4"
    if output_file.exists():
        output_mtime = output_file.stat().st_mtime
        if all(v.stat().st_mtime <= output_mtime for v in scene_videos):
            logger.info(f"Already concatenated: {output_file.name}")
            return output_file
        logger.info(f"Scene videos newer than {output_file.name} — re-concatenating")
        output_file.unlink()

    logger.info(f"Concatenating {len(scene_videos)} scenes for {section_name}...")
    return concat_videos(scene_videos, section_name)


if __name__ == "__main__":
    # Usage:
    #   python -m src.assembler.assemble timeline_section_2_scene_3       (single scene)
    #   python -m src.assembler.assemble section_2                        (all scenes for section)
    #   python -m src.assembler.assemble section_2 --concat               (assemble + concat into section video)
    #   python -m src.assembler.assemble --watch
    import sys
    import time
    import argparse
    from concurrent.futures import ThreadPoolExecutor

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="Assemble narration + video")
    parser.add_argument("target", type=str, nargs="?", help="Scene name or section name")
    parser.add_argument("--watch", action="store_true", help="Watch narration dir for new files")
    parser.add_argument("--concat", action="store_true", help="Concatenate scene videos into one section video")
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
            if args.concat:
                concat_section(target)
        _executor.shutdown(wait=True)
    else:
        parser.print_help()
