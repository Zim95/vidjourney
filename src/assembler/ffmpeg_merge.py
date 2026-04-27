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


SUBTITLE_STYLE = (
    "FontName=Arial,FontSize=24,"
    "PrimaryColour=&Hffffff&,OutlineColour=&H000000&,"
    "BorderStyle=1,Outline=2,Shadow=1,"
    "Alignment=2,MarginV=60"
)


def merge_audio_video(
    video_path: Path,
    audio_path: Path,
    output_name: str,
    subtitles_path: Path | None = None,
) -> Path:
    """Merge silent video with narration audio. Burns in subtitles if provided."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"{output_name}.mp4"

    if output_file.exists():
        return output_file

    cmd: list[str] = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
    ]

    has_subs = (
        subtitles_path is not None
        and subtitles_path.exists()
        and subtitles_path.stat().st_size > 0
    )

    if has_subs:
        # Burn-in requires re-encoding video. Use the absolute path so the
        # subtitles filter resolves correctly regardless of cwd.
        srt_abs = str(subtitles_path.resolve())
        cmd.extend([
            "-vf", f"subtitles={srt_abs}:force_style='{SUBTITLE_STYLE}'",
            "-c:v", "libx264",
            "-c:a", "aac",
        ])
    else:
        cmd.extend([
            "-c:v", "copy",
            "-c:a", "aac",
        ])

    cmd.extend(["-shortest", str(output_file)])

    subprocess.run(cmd, check=True, capture_output=True)

    logger.info(f"Assembled output: {output_file.name}{' (with subtitles)' if has_subs else ''}")
    return output_file


def concat_wavs(wav_paths: list[Path], output_path: Path) -> Path:
    """Concatenate multiple WAV files into one, preserving sample rate via copy."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        return output_path

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        list_file = Path(f.name)
        for wav in wav_paths:
            f.write(f"file '{wav.resolve()}'\n")

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_file), "-c", "copy", str(output_path)],
            check=True,
            capture_output=True,
        )
    finally:
        list_file.unlink(missing_ok=True)

    logger.info(f"Concatenated {len(wav_paths)} wav(s) → {output_path.name}")
    return output_path


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
