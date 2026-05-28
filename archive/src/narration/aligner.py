"""
Forced alignment of narration WAV against the script text.

Uses faster-whisper (CTranslate2-based Whisper) to produce word-level timestamps.
The result is a sidecar JSON next to the WAV, so visual timing and subtitle
generation can both anchor to real spoken-word times instead of word-count
estimates.

Voice-agnostic by design: the aligner reads the WAV and doesn't care which TTS
produced it. Swap Piper for any other voice; alignment recomputes per WAV.

Usage:
    from src.narration.aligner import align_narration
    words = align_narration(wav_path, voiceover_text)
    # → [{"word": "When", "start": 0.04, "end": 0.21}, ...]
"""
import json
from pathlib import Path

from src.utils import logger, timer
from src.config.constants import GROUPING_ALIGNER_MODEL


_model = None


def _get_model():
    """Load the Whisper model once and reuse it.

    int8 quantization keeps RAM under ~300 MB for base.en on Mac.
    """
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info(f"Loading Whisper model: {GROUPING_ALIGNER_MODEL} (int8)")
        _model = WhisperModel(GROUPING_ALIGNER_MODEL, device="cpu", compute_type="int8")
    return _model


def alignment_sidecar_path(wav_path: Path) -> Path:
    """Sidecar JSON path for a given WAV — same stem with .alignment.json suffix."""
    return wav_path.with_suffix(".alignment.json")


@timer(label="Align narration")
def align_narration(wav_path: Path, text: str) -> list[dict]:
    """Return per-word timestamps for the spoken WAV.

    Each entry: {"word": str, "start": float, "end": float}.

    Cached as a JSON sidecar at <wav>.alignment.json — re-runs are instant.
    `text` biases the model toward the known script (initial_prompt) so
    technical jargon ("API", entity names) transcribes consistently.
    """
    sidecar = alignment_sidecar_path(wav_path)
    if sidecar.exists():
        return json.loads(sidecar.read_text(encoding="utf-8"))

    if not wav_path.exists():
        logger.warning(f"WAV not found for alignment: {wav_path}")
        return []

    model = _get_model()
    segments, _info = model.transcribe(
        str(wav_path),
        word_timestamps=True,
        language="en",
        initial_prompt=text or None,
        vad_filter=False,
    )

    words: list[dict] = []
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            words.append({
                "word": (w.word or "").strip(),
                "start": float(w.start),
                "end": float(w.end),
            })

    sidecar.write_text(json.dumps(words, indent=2), encoding="utf-8")
    logger.info(f"Wrote alignment sidecar: {sidecar.name} ({len(words)} words)")
    return words


def load_alignment(wav_path: Path) -> list[dict] | None:
    """Read an existing sidecar without running the model. Returns None if absent."""
    sidecar = alignment_sidecar_path(wav_path)
    if not sidecar.exists():
        return None
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Failed to load alignment sidecar {sidecar.name}: {exc}")
        return None
