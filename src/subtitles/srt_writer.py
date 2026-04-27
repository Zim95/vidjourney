"""
SRT subtitle generator.

Reads a timeline file and produces a .srt for the scene. The voiceover is split
into short readable cards (~CHUNK_WORDS each).

For list scenes, a sidecar `<timeline>.parts.json` carries the actual per-segment
audio durations measured at narration time. We anchor each card to its segment's
window — drift is bounded to within a single segment instead of the whole scene.

Falls back to whole-scene word-count distribution when no sidecar exists.

Soft-hyphenation artifacts from PDF extraction are cleaned up.
"""
import json
import re
from pathlib import Path

from src.config.constants import (
    SUBTITLE_CHUNK_WORDS as CHUNK_WORDS,
    SUBTITLE_MAX_CHARS_PER_LINE as MAX_CHARS_PER_LINE,
)


def _format_time(seconds: float) -> str:
    """Convert seconds to SRT time format: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, total_ms = divmod(total_ms, 3600 * 1000)
    minutes, total_ms = divmod(total_ms, 60 * 1000)
    secs, ms = divmod(total_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _clean_text(text: str) -> str:
    """Strip PDF artifacts: soft hyphens that split words across lines."""
    # ‐ (HYPHEN) or ­ (SOFT HYPHEN) followed by whitespace = mid-word break
    text = re.sub(r"[‐­]\s+", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _split_sentences(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [s.strip() for s in sentences if s.strip()]


def _chunk_words(sentence: str, words_per_chunk: int) -> list[str]:
    """Split a sentence into chunks of ~words_per_chunk words."""
    words = sentence.split()
    if len(words) <= words_per_chunk:
        return [sentence]
    chunks: list[str] = []
    for i in range(0, len(words), words_per_chunk):
        chunks.append(" ".join(words[i : i + words_per_chunk]))
    return chunks


def _wrap_lines(text: str, max_chars: int) -> str:
    """Insert a newline if the text exceeds max_chars (max 2 lines)."""
    if len(text) <= max_chars:
        return text
    words = text.split()
    line1: list[str] = []
    line2: list[str] = []
    current_len = 0
    for w in words:
        if current_len + len(w) + (1 if line1 else 0) <= max_chars and not line2:
            line1.append(w)
            current_len += len(w) + (1 if len(line1) > 1 else 0)
        else:
            line2.append(w)
    if line2:
        return " ".join(line1) + "\n" + " ".join(line2)
    return text


def _parse_timeline(timeline_path: Path) -> tuple[str, float]:
    """Extract VOICEOVER text and TOTAL_DURATION (seconds) from a timeline file."""
    content = timeline_path.read_text(encoding="utf-8", errors="replace")

    duration_match = re.search(r"TOTAL_DURATION:\s*([\d.]+)", content)
    duration = float(duration_match.group(1)) if duration_match else 0.0

    voiceover_match = re.search(
        r"VOICEOVER:\s*(.*?)(?=\n\s*\n|\nTIMELINE:|\Z)",
        content,
        re.DOTALL,
    )
    voiceover = voiceover_match.group(1).strip() if voiceover_match else ""
    return voiceover, duration


def _build_cards_for_segment(text: str) -> list[tuple[str, int]]:
    """Split text into (chunk, word_count) cards by sentence then word-chunking."""
    text = _clean_text(text)
    sentences = _split_sentences(text) or [text]
    cards: list[tuple[str, int]] = []
    for sentence in sentences:
        for chunk in _chunk_words(sentence, CHUNK_WORDS):
            cards.append((chunk, max(1, len(chunk.split()))))
    return cards


def _format_card(idx: int, start: float, end: float, text: str) -> str:
    return (
        f"{idx}\n"
        f"{_format_time(start)} --> {_format_time(end)}\n"
        f"{_wrap_lines(text, MAX_CHARS_PER_LINE)}\n"
    )


def _read_parts_sidecar(timeline_path: Path) -> list[dict] | None:
    """Read the per-part durations sidecar if it exists.

    Sidecar shape: [{"text": "...", "duration": 9.24}, ...]
    """
    sidecar = timeline_path.with_name(timeline_path.stem + ".parts.json")
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(data, list) and all(
            isinstance(p, dict) and "text" in p and "duration" in p for p in data
        ):
            return data
    except (json.JSONDecodeError, OSError):
        return None
    return None


def generate_srt(timeline_path: Path, output_srt: Path) -> Path:
    """Generate an SRT file from a timeline. Returns the path written."""
    output_srt.parent.mkdir(parents=True, exist_ok=True)

    voiceover, duration = _parse_timeline(timeline_path)
    if not voiceover or duration <= 0:
        output_srt.write_text("", encoding="utf-8")
        return output_srt

    parts = _read_parts_sidecar(timeline_path)

    blocks: list[str] = []
    card_idx = 0

    if parts:
        # Per-segment timing: anchor each segment's cards inside its measured window
        cursor = 0.0
        for segment in parts:
            seg_dur = float(segment["duration"])
            seg_text = str(segment["text"])
            seg_cards = _build_cards_for_segment(seg_text)
            if not seg_cards:
                cursor += seg_dur
                continue
            total_words = sum(w for _, w in seg_cards)
            inner_cursor = cursor
            for j, (text, words) in enumerate(seg_cards):
                slice_dur = (words / total_words) * seg_dur
                start = inner_cursor
                end = inner_cursor + slice_dur if j < len(seg_cards) - 1 else cursor + seg_dur
                card_idx += 1
                blocks.append(_format_card(card_idx, start, end, text))
                inner_cursor = end
            cursor += seg_dur
    else:
        # No sidecar — distribute time across the whole scene by word count
        cards = _build_cards_for_segment(voiceover)
        if not cards:
            output_srt.write_text("", encoding="utf-8")
            return output_srt
        total_words = sum(w for _, w in cards)
        cursor = 0.0
        for j, (text, words) in enumerate(cards):
            slice_dur = (words / total_words) * duration
            start = cursor
            end = cursor + slice_dur if j < len(cards) - 1 else duration
            card_idx += 1
            blocks.append(_format_card(card_idx, start, end, text))
            cursor = end

    output_srt.write_text("\n".join(blocks), encoding="utf-8")
    return output_srt


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Generate SRT from a timeline file")
    parser.add_argument("timeline_file", type=str)
    parser.add_argument("--output", type=str, help="Output SRT path (default: <timeline>.srt)")
    args = parser.parse_args()

    timeline = Path(args.timeline_file)
    if not timeline.exists():
        print(f"File not found: {timeline}")
        sys.exit(1)

    out = Path(args.output) if args.output else timeline.with_suffix(".srt")
    generate_srt(timeline, out)
    print(f"Wrote: {out}")
