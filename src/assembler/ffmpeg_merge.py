"""
FFmpeg merge: combines video and audio into final output.
"""
import subprocess
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
