"""
FFmpeg merge: combines video and audio into final output, and concatenates
per-scene outputs into a single section video.
"""
import subprocess
import tempfile
from pathlib import Path

from src.utils import logger
from src.config.constants import GROUPING_OUTPUT_DIR


OUTPUT_DIR = GROUPING_OUTPUT_DIR


def merge_audio_video(video_path: Path, audio_path: Path, output_name: str) -> Path:
    """Merge silent video with narration audio using ffmpeg."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"{output_name}.mp4"

    if output_file.exists():
        return output_file

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(output_file),
        ],
        check=True,
        capture_output=True,
    )

    logger.info(f"Assembled output: {output_file.name}")
    return output_file


def concat_videos(video_paths: list[Path], output_name: str) -> Path:
    """Concatenate multiple mp4 files in order into a single video."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"{output_name}.mp4"

    if output_file.exists():
        return output_file

    # ffmpeg concat demuxer requires a list file with "file '<path>'" per line
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        list_file = Path(f.name)
        for video in video_paths:
            f.write(f"file '{video.resolve()}'\n")

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                "-c:v", "libx264",
                "-c:a", "aac",
                str(output_file),
            ],
            check=True,
            capture_output=True,
        )
    finally:
        list_file.unlink(missing_ok=True)

    logger.info(f"Concatenated {len(video_paths)} scenes → {output_file.name}")
    return output_file
