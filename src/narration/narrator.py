"""
TTS audio generation using Piper.
"""
import re
import subprocess
import wave
from pathlib import Path

from piper import PiperVoice

from piper import SynthesisConfig
from src.utils import logger, timer
from src.config.constants import (
    GROUPING_NARRATION_DIR,
    GROUPING_PIPER_MODEL,
    GROUPING_PIPER_SPEAKER_ID,
    GROUPING_PIPER_LENGTH_SCALE,
)


NARRATION_DIR = GROUPING_NARRATION_DIR

_voice = None


def _get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        _voice = PiperVoice.load(GROUPING_PIPER_MODEL)
    return _voice


def parse_voiceover(timeline_file: Path) -> str:
    """Extract VOICEOVER text from a timeline file."""
    content = timeline_file.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'VOICEOVER:\s*"?(.+?)"?\s*$', content, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else ""


@timer(label="Generate narration")
def generate_narration(timeline_file: Path) -> Path:
    """Generate .wav narration from timeline's VOICEOVER text."""
    NARRATION_DIR.mkdir(parents=True, exist_ok=True)
    output_file = NARRATION_DIR / f"{timeline_file.stem}.wav"

    if output_file.exists():
        logger.info(f"Narration already exists: {output_file.name}")
        return output_file

    voiceover = parse_voiceover(timeline_file)
    if not voiceover:
        logger.warning(f"No voiceover text in {timeline_file.name}")
        return output_file

    logger.info(f"Generating TTS: {timeline_file.name} → {output_file.name}")
    voice = _get_voice()

    syn_config = SynthesisConfig(
        speaker_id=GROUPING_PIPER_SPEAKER_ID,
        length_scale=GROUPING_PIPER_LENGTH_SCALE,
    )

    all_audio = bytearray()
    for audio_chunk in voice.synthesize(voiceover, syn_config):
        all_audio.extend(audio_chunk.audio_int16_bytes)

    with wave.open(str(output_file), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(voice.config.sample_rate)
        wav_file.writeframes(bytes(all_audio))

    logger.info(f"Narration written: {output_file.name}")
    return output_file


def narrate_text(text: str, output_path: Path) -> Path:
    """TTS arbitrary text to a WAV file. Always overwrites the destination —
    callers that want caching must check content equivalence themselves
    (skip-if-exists by filename was a stale-cache footgun when block content
    shifted but the filename slot stayed the same)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        # Write a tiny silent wav to avoid downstream errors
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"")
        return output_path

    voice = _get_voice()
    syn_config = SynthesisConfig(
        speaker_id=GROUPING_PIPER_SPEAKER_ID,
        length_scale=GROUPING_PIPER_LENGTH_SCALE,
    )
    all_audio = bytearray()
    for audio_chunk in voice.synthesize(text, syn_config):
        all_audio.extend(audio_chunk.audio_int16_bytes)

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(voice.config.sample_rate)
        wav_file.writeframes(bytes(all_audio))

    return output_path


def get_audio_duration(wav_path: Path) -> float:
    """Read WAV duration in seconds (uses Python's wave module — no ffprobe needed)."""
    with wave.open(str(wav_path), "rb") as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
        return frames / float(rate) if rate else 0.0
