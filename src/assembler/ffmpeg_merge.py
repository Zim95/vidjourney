"""
FFmpeg audio concatenation for the scroll renderer.
"""
import subprocess
import tempfile
from pathlib import Path

from src.utils import logger
from src.scheduler import subprocess_slot


def concat_wavs(wav_paths: list[Path], output_path: Path) -> Path:
    """Concatenate multiple WAV files into one, preserving sample rate via copy.

    Re-runs when any input is newer than the output. The previous
    unconditional skip-if-exists shipped stale concatenations whenever the
    per-block wavs were regenerated (e.g., after a re-narration).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_mtime = output_path.stat().st_mtime
        if all(p.exists() and p.stat().st_mtime <= output_mtime for p in wav_paths):
            return output_path

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        list_file = Path(f.name)
        for wav in wav_paths:
            f.write(f"file '{wav.resolve()}'\n")

    try:
        with subprocess_slot():
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
