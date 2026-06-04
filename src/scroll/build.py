"""
Scroll-renderer prototype entry point.

End-to-end build for one section in the scroll architecture:

1. Read ``pipeline/groups/content_groups/section_N.txt`` (same input the
   existing pipeline consumes — no upstream changes).
2. Convert ContentGroups into a flat list of ``Block``s. The same long-item
   expansion (``_expand_list_items``) and concept-card extraction from
   ``llm_timeline`` is reused so summaries / parenthetical bullets / nested
   levels carry over unchanged.
3. Pre-narrate each block via Piper, measure wav durations, concatenate
   into one ``pipeline/scroll/narration/section_N.wav``.
4. Compute a vertical layout: each block claims a y-range proportional to
   its narration duration so a linear camera scroll keeps the
   currently-narrated block centered (modulo block-height vs window-rate
   slack).
5. Write a JSON instructions file at
   ``pipeline/scroll/instructions/section_N.json`` describing every block's
   final layout position + the camera animation timeline.
6. Invoke manim with ``ScrollScene`` to render
   ``pipeline/scroll/output/section_N.mp4`` (silent), then ffmpeg-merge
   with the narration wav to produce the final mp4.

The old per-scene pipeline is untouched; everything written here is in
``pipeline/scroll/`` so a fallback is just "use ``pipeline/output/``
instead." This is intentional — the prototype's value is comparing
side-by-side, not replacing the working baseline.

Usage:
    python -m src.scroll.build 1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from src.utils import logger
from src.config.constants import (
    GROUPING_CONTENT_GROUPS_DIR,
    MANIM_PYTHON,
    MANIM_QUALITY,
)
from src.scene_grouping.llm_grouper import deserialize_groups
from src.scene_grouping.llm_classifier import classify_paragraph
from src.scene_grouping.llm_quotes import extract_quote
from src.narration.narrator import narrate_text, get_audio_duration
from src.assembler.ffmpeg_merge import concat_wavs

from src.scroll.blocks import Block


def _split_sentences(text: str) -> list[str]:
    # PDF extraction gives mid-word soft-hyphens with a following space/newline
    # (e.g. "espe‐ cially" should become "especially"). Strip hyphen-plus-
    # whitespace together — stripping only the hyphen would leave "espe cially".
    text = re.sub(r"[‐­]\s+", "", text)
    text = text.replace("­", "").replace("‐", "")
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [s.strip() for s in sentences if s.strip()]


def _strip_bullet(text: str) -> str:
    return re.sub(r"^[•·\-*]\s*", "", text).strip()


SCROLL_DIR = Path("pipeline/scroll")
INSTRUCTIONS_DIR = SCROLL_DIR / "instructions"
NARRATION_DIR = SCROLL_DIR / "narration"
OUTPUT_DIR = SCROLL_DIR / "output"

# --- Tuning parameters (the whole point of the scroll architecture) ---
#
# The scroll mechanism in one rule: during each block's narration window,
# the camera moves from "this block sits at upper-third of viewport" to
# "the NEXT block sits at upper-third of viewport." Per-block scroll speed
# is therefore (block_height + INTER_BLOCK_PAD) / block_duration.
#
# Short blocks (bullets, ~0.5 units tall, 5s narration) → camera moves
# ~0.15 units/sec — looks like a settle.
# Tall blocks (images, ~6 units tall, 3-4s view) → camera moves
# ~1.5-2 units/sec — looks like a continuous flow.
#
# One rule, two behaviors emerge from content shape. No mode switching.

# Inter-block gap (vertical space below each block). Matches the page-break
# pipeline's _LIST_ITEM_GAP so adjacent bullets feel as tight as before.
INTER_BLOCK_PAD = 0.25

# Where the currently-narrating block's top sits in the viewport, in manim
# y-units above the camera center. Positive = upper half; the active block
# appears near the top so newer content has room below it. 0 would center.
CAMERA_LEAD = 1.8

# Natural heights per block kind (in manim units). Bullets now use
# sentence-length text so their height is computed dynamically from
# wrapped-line count rather than a fixed constant (see ``_block_height``).
BLOCK_HEIGHT = {
    "heading": 1.6,
    # Image close to full viewport (manim default frame is 8 units tall).
    # User asked for images to "cover the whole area" + continuously scroll.
    "image": 6.0,
    "quote": 2.4,
}

# Bullet wrap parameters — must stay in sync with ScrollScene's constants
# so layout-math matches actual render. Tightened from 70→55 so single-line
# bullets don't exceed BULLET_TARGET_WIDTH and trigger scale_to_fit_width
# (which would visibly shrink the affected bullets relative to others).
BULLET_WRAP_CHARS = 55
BULLET_LINE_HEIGHT = 0.4  # height per wrapped line in manim units
BULLET_GLYPH_PREFIX_LEN = 3  # "•  " = 3 chars consumed by the bullet glyph + spacing

# Quote wrap parameters — keep in sync with ScrollScene._quote_mobject.
QUOTE_WRAP_CHARS = 60
QUOTE_LINE_HEIGHT = 0.5
QUOTE_ATTRIB_HEIGHT = 0.35
QUOTE_ATTRIB_BUFF = 0.3  # gap between quote and attribution

# Image rendering bounds — must match ScrollScene constants so the layout
# claims the right vertical space for the actual rendered image size.
IMAGE_TARGET_HEIGHT = 6.0
IMAGE_TARGET_WIDTH = 12.0

# For image blocks with no narration (book cover, decorative figures), we
# synthesize a view duration so the camera spends time scrolling across the
# image. SECONDS_PER_IMAGE_UNIT seconds of viewing time per manim y-unit of
# image height. ~0.7 = a 6-unit image takes ~4.2s to scroll through.
SECONDS_PER_IMAGE_UNIT = 0.7

BULLET_GLYPH = ["•", "◦", "▸"]


def _count_wrap_lines(text: str, wrap_chars: int) -> int:
    """Mirror of ListItemShape's soft-wrap line count. Keep in sync with
    ScrollScene._soft_wrap so layout y-positions match actual rendered text."""
    if len(text) <= wrap_chars:
        return 1
    words = text.split()
    if not words:
        return 1
    lines = 1
    line_len = 0
    for w in words:
        if line_len and line_len + len(w) + 1 > wrap_chars:
            lines += 1
            line_len = len(w)
        else:
            line_len += len(w) + (1 if line_len else 0)
    return lines


def _image_rendered_height(image_path: str) -> float:
    """Return the rendered height (manim units) for an image, mirroring
    ScrollScene._image_mobject's scale logic: try fit-to-height, fall back
    to fit-to-width for wide-aspect images. Returns IMAGE_TARGET_HEIGHT for
    missing files (the shape will produce nothing; layout reserves the
    nominal space).
    """
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            w, h = img.size
    except Exception:
        return IMAGE_TARGET_HEIGHT
    if h == 0:
        return IMAGE_TARGET_HEIGHT
    aspect = w / h
    width_at_target_h = IMAGE_TARGET_HEIGHT * aspect
    if width_at_target_h <= IMAGE_TARGET_WIDTH:
        return IMAGE_TARGET_HEIGHT  # height-fit, image stays at 6 units tall
    # Wide-aspect: clamped to target width, height shrinks proportionally.
    return IMAGE_TARGET_WIDTH / aspect


def _block_height(block: Block) -> float:
    if block.kind == "bullet":
        # Sentence-length bullets vary widely (5 words → 1 line, 40 words →
        # 4 lines). Compute height from wrapped-line count so tall bullets
        # claim the y-space they actually need.
        full = "•  " + (block.display or "")  # glyph prefix consumes chars
        lines = _count_wrap_lines(full, BULLET_WRAP_CHARS)
        return max(0.5, lines * BULLET_LINE_HEIGHT + 0.1)
    if block.kind == "quote":
        # Quote height varies with how many wrapped lines the quote body
        # takes, plus an attribution line below (if present) plus the buff
        # between them.
        body = f'"{block.display}"' if block.display else ""
        lines = _count_wrap_lines(body, QUOTE_WRAP_CHARS) if body else 1
        h = lines * QUOTE_LINE_HEIGHT
        if block.attribution:
            h += QUOTE_ATTRIB_BUFF + QUOTE_ATTRIB_HEIGHT
        return max(1.0, h + 0.2)  # small padding around the block
    if block.kind == "image" and block.resource_path:
        h = _image_rendered_height(block.resource_path)
        # If the image has a caption rendered below it, the natural-height
        # claimed by the block has to include the caption's text + the buff
        # between image and caption. Without this, the next block crowds in
        # under the caption.
        if block.caption:
            h += 0.2 + 0.35  # buff + CAPTION_HEIGHT (must match ScrollScene)
        return h
    return BLOCK_HEIGHT.get(block.kind, 0.8)


# --- ContentGroups → Blocks ---


# A sentence longer than this gets split on internal punctuation into
# multiple bullets. Keeps any single bullet within 3-4 wrapped lines on
# screen and prevents the scroll from stalling on one huge bullet.
MAX_BULLET_WORDS = 25

# Boundaries we'll split a long sentence on, in priority order. Each pattern
# is a regex anchored at a phrase that signals a clear structural break.
#
# Only structural punctuation now — semicolon, em-dash, colon. The earlier
# comma+conjunction rule (split at "X, but Y" or "X, and Y") was too
# aggressive: it fragmented coordinate clauses like "...follows many people,
# and is followed by many people" into two stub bullets. A bullet that
# wraps to 3-4 lines is better than a sentence broken into stub fragments
# that lose their grammatical subject.
_LONG_SENTENCE_SPLITS = [
    r"(?<=;)\s+",            # semicolon
    r"(?<=—)\s+|\s+(?=—)",   # em-dash
    r"(?<=:)\s+",            # colon (lead-in to a clause)
]


def _merge_short_stubs(parts: list[str]) -> list[str]:
    """Merge any sub-5-word part into its neighbour so a long-sentence split
    can't leave stub bullets like "or something else" stranded after a
    long preceding clause. We merge regardless of the resulting length —
    a slightly-over-budget bullet is better than a 3-word fragment.
    """
    merged: list[str] = []
    for p in parts:
        if merged and len(p.split()) < 5:
            merged[-1] = merged[-1] + " " + p
        elif merged and len(merged[-1].split()) < 5:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


def _split_long_sentence(sentence: str, level: int = 0) -> list[str]:
    """Break sentences longer than ``MAX_BULLET_WORDS`` on natural punctuation
    boundaries. Returns a list of sub-bullets — same level as the source —
    each within the word budget. If no natural boundary exists, returns the
    original sentence (it'll wrap visually instead of breaking).

    A split is only accepted when every right-hand half starts with a
    capital letter — that's what marks a structurally independent clause.
    Splits on ``:`` or ``;`` mid-thought (lowercase continuation like
    "if an attacker..." or "therefore it is...") would orphan obvious
    fragments, so we reject those and try the next pattern (or fall back
    to leaving the sentence whole).
    """
    if len(sentence.split()) <= MAX_BULLET_WORDS:
        return [sentence]

    for pattern in _LONG_SENTENCE_SPLITS:
        parts = re.split(pattern, sentence)
        parts = [p.strip().strip(",;:—").strip() for p in parts if p and p.strip()]
        if len(parts) <= 1:
            continue
        if not all(p and p[0].isupper() for p in parts[1:]):
            continue
        merged = _merge_short_stubs(parts)
        result: list[str] = []
        for p in merged:
            if len(p.split()) > MAX_BULLET_WORDS:
                result.extend(_split_long_sentence(p, level))
            else:
                result.append(p)
        return result

    return [sentence]


_STUB_PATTERN = re.compile(r"^[\d]+[.)]?$|^[a-zA-Z][.)]$")

# Arabic-numeral enumeration markers — these ARE real list markers and
# trigger the nesting state machine ("1. Posting a tweet... 2. Maintain..."
# turns into nested L+1 items).
_LEADING_LIST_MARKER_RE = re.compile(r"^\(?\d+[.)]\s+")
# Trailing orphan marker — sentence ending in ": 1.", "; 2.", ". 3.", etc.
# Lookbehind for the lead-in punctuation prevents stripping legitimate
# trailing numerals like "Read Chapter 1." (no `[:;.!?]` before the digit).
_TRAILING_LIST_MARKER_RE = re.compile(r"(?<=[:;.!?])\s+\(?\d{1,2}[.)]\s*$")
# Broader strip pattern — also catches Roman-numeral footnote refs that
# the PDF ingestion leaks into paragraph text ("ii. Literature on the
# relational model..."). Footnotes are NOT list items, so they're stripped
# from display but don't trigger the nesting state machine.
_STRIP_LEADING_MARKER_RE = re.compile(r"^\(?(?:\d+|[ivxIVX]{1,4})[.)]\s+")


def _strip_list_markers(s: str) -> str:
    """Remove inline enumeration markers from sentence edges. Leaves any
    middle-of-sentence numerals alone. Strips Arabic AND Roman numeral
    leading markers; only Arabic trailing markers (the only kind that
    appears as the ': 1.' lead-in pattern)."""
    s = _STRIP_LEADING_MARKER_RE.sub("", s.lstrip())
    s = _TRAILING_LIST_MARKER_RE.sub("", s.rstrip())
    return s.strip()


def _is_stub_sentence(s: str) -> bool:
    """Return True for sentences the splitter manufactured by accident —
    list-item markers like "1.", "2.", "(a)" that the in-line PDF text
    contains and that ``_split_sentences`` mistakes for sentence boundaries.
    """
    s = s.strip()
    return bool(_STUB_PATTERN.match(s)) or len(s.split()) < 3


# Bullets can nest at most this deep automatically via inline-marker
# detection. Renderer (manim_scene.BULLET_GLYPH) supports 3 levels (0-2).
_MAX_AUTO_NEST_LEVEL = 2


def _sentence_bullets(text: str, level: int = 0) -> list[Block]:
    """Sentence-split a paragraph into one bullet per sentence, with inline
    enumeration detection: when the source contains numbered markers ("1.
    Posting a tweet... 2. Maintain a cache..."), the marked items become
    nested sub-bullets (level + 1) under the lead-in sentence that
    introduces them.

    Display text equals narration text — no summary layer. Markers
    themselves are stripped (the bullet glyph already signals "list item").

    The state machine: once any sentence has a leading or trailing
    numbered marker, the paragraph is "in a list" — subsequent sentences
    render at the elevated level until the paragraph ends. Continuation
    sentences of an item (no marker, no obvious break) inherit the item's
    level so they stay visually grouped with their parent item.

    Stubs (1-2 word fragments left by sentence splitting) merge into their
    neighbour at the neighbour's level. Long sentences split further on
    structural punctuation (``_split_long_sentence``).
    """
    raw = _split_sentences(text)
    if not raw:
        return []

    # First pass: absorb sentence stubs (the "2." artifact that splits off
    # when the regex sees ". M" mid-paragraph) into the FOLLOWING sentence.
    # This has to happen BEFORE marker detection so the merged sentence
    # (e.g. "2. Maintain a cache...") starts with a recognizable marker.
    merged_raw: list[str] = []
    pending: str = ""
    for s in raw:
        s = s.strip()
        if not s:
            continue
        if _is_stub_sentence(s):
            pending = (pending + " " + s).strip() if pending else s
            continue
        if pending:
            s = pending + " " + s
            pending = ""
        merged_raw.append(s)
    if pending and merged_raw:
        merged_raw[-1] = merged_raw[-1] + " " + pending

    # Detect markers on the post-stub-merge sentences. A leading marker
    # means "this sentence IS an item"; a trailing marker means "this
    # sentence is the LEAD-IN and the next sentence begins the item body."
    leading = [bool(_LEADING_LIST_MARKER_RE.match(s.lstrip())) for s in merged_raw]
    trailing = [bool(_TRAILING_LIST_MARKER_RE.search(s.rstrip())) for s in merged_raw]

    # Per-sentence level via a state machine. Once we enter a list we stay
    # in it until paragraph end so multi-sentence items don't visually break
    # apart at the item/continuation boundary.
    sentence_levels: list[int] = []
    in_list = False
    item_level = min(level + 1, _MAX_AUTO_NEST_LEVEL)
    for i in range(len(merged_raw)):
        if leading[i]:
            in_list = True
            this_level = item_level
        elif trailing[i]:
            # Trailing marker = "next sentence starts an item." If we haven't
            # entered the list yet, THIS sentence is the lead-in and stays
            # at parent level. If we're already in a list, THIS sentence is
            # itself an item that happens to be followed by another item's
            # marker — keep it at item level.
            this_level = item_level if in_list else level
            in_list = True
        elif in_list:
            this_level = item_level
        else:
            this_level = level
        sentence_levels.append(this_level)

    # Now strip markers from the displayed text. The level state above
    # already captures the structural meaning the markers conveyed.
    sentences = [_strip_list_markers(s).strip() for s in merged_raw]

    bullets: list[Block] = []
    for sent, lv in zip(sentences, sentence_levels):
        if not sent:
            continue
        for chunk in _split_long_sentence(sent, lv):
            chunk = chunk.strip()
            if chunk:
                bullets.append(Block(kind="bullet", text=chunk, display=chunk, level=lv))
    return bullets


def _groups_to_blocks(groups: list, section_name: str) -> list[Block]:
    """Walk content groups in source order and produce blocks.

    Display-text policy: **show what's being narrated, verbatim.** We drop
    the summary layer entirely — no SUMMARY_PROMPT, no concept-card body
    abstraction, no listify summaries. Every bullet's on-screen text equals
    its narration text. Paragraphs sentence-split into bullets so the
    camera has something to advance to as each sentence is read.

    Why: with summaries, the viewer's eye reads a 5-word abstract in 1s
    then has to wait while the audio reads the full 25-word sentence. The
    eye/ear race forces context-switching for every block. Showing what's
    narrated removes the race — the on-screen text is the audio, in
    lockstep.

    Block types emitted:
      - heading: section/part heading
      - quote: full quote text + attribution (centered, italic)
      - image: source figure shown inline near-full-viewport
      - bullet: one sentence per bullet (level 0; nesting deferred)
    """
    blocks: list[Block] = []
    for group in groups:
        if group.kind == "heading":
            text = group.anchor.text.strip()
            blocks.append(Block(kind="heading", text=text, display=text))
            continue

        if group.kind == "quote":
            # Quote was deterministically detected upstream (ingestion or the
            # grouper's safety-net pass) — anchor.text is the full quote +
            # attribution string. extract_quote pulls them apart for display.
            quote_text = group.anchor.text.strip()
            q = extract_quote(quote_text)
            blocks.append(Block(
                kind="quote",
                text=quote_text,
                display=q.text,
                attribution=q.attribution,
            ))
            continue

        if group.kind != "paragraph":
            # Standalone list / standalone resource groups. Skip for now —
            # the sections we've targeted don't have them.
            continue

        intro_text = group.anchor.text.strip()
        list_items = list(group.list_items)
        resources = group.resources
        cap_map = group.caption_for_resource

        # LLM-classifier fallback for quotes the deterministic detector
        # didn't catch — bare-name attributions (e.g. "—Donald Knuth") and
        # non-Gregorian years (BCE, year-ranges like "1265-1274"). Year-bearing
        # attributions are already routed via the "quote" group kind above
        # and don't reach this branch.
        if not list_items and not resources:
            verdict = classify_paragraph(intro_text)
            if verdict == "quote":
                q = extract_quote(intro_text)
                blocks.append(Block(
                    kind="quote",
                    text=intro_text,
                    display=q.text,
                    attribution=q.attribution,
                ))
                continue

        # Everything else: paragraph anchor sentence-splits into level-0
        # bullets (lead-in / context), then any resources show inline, then
        # explicit LIST_ITEMs from the source become level-1 sub-bullets
        # nested under the lead-in. Each LIST_ITEM also gets long-sentence
        # splitting so book-style multi-clause items don't become 5-line
        # walls of text.
        if intro_text:
            blocks.extend(_sentence_bullets(intro_text, level=0))

        # Emit every resource the group carries, in source order. Earlier
        # versions only took resources[0], which silently dropped Figure 1-3
        # in section 10 group 2 (which has two images alongside one
        # paragraph). Multiple images render as consecutive stacked image
        # blocks with their respective captions.
        for res in resources:
            blocks.append(Block(
                kind="image",
                text="",
                display="",
                resource_path=res.text,
                caption=(cap_map.get(res.text) or ""),
            ))

        for item in list_items:
            body = _strip_bullet(item.text)
            if not body:
                continue
            blocks.extend(_sentence_bullets(body, level=1))

    return blocks


# --- Pre-narrate ---


def _make_silence_wav(duration: float, out_path: Path) -> Path:
    """Generate a silent WAV of the given duration. Used as a stand-in audio
    track during image blocks that have no narration — the camera scrolls
    across the image; the silence keeps audio cursor in sync with video.
    """
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono",
        "-t", f"{duration:.3f}",
        str(out_path),
    ], check=True, capture_output=True)
    return out_path


def _synthesized_view_duration(block: Block) -> float:
    """For blocks that need on-screen time without narration (typically
    image blocks with no caption), pick a duration based on visual size.
    """
    if block.kind == "image":
        natural_h = BLOCK_HEIGHT.get("image", 6.0)
        return max(2.0, natural_h * SECONDS_PER_IMAGE_UNIT)
    return 0.0


# Per-kind reading rates (seconds per visible BULLET_LINE_HEIGHT-tall line).
# Used to compute the minimum on-screen time for a block — if Piper narrates
# faster than this, we append silence so the camera doesn't scroll past
# unreadable content. Tuned for an audiobook/lecture feel at 1.5s/line for
# wrapped prose, slightly faster for headings (large, easy to skim) and
# slower for quotes (italic, harder to parse).
_SEC_PER_LINE = {
    "heading": 1.0,
    "bullet": 1.5,
    "paragraph": 1.5,
    "quote": 2.0,
}
# Image blocks use a per-unit rate instead of per-line (no "lines").
_SEC_PER_IMAGE_UNIT_MIN = 2.0


def _min_view_time(block: Block) -> float:
    """Minimum time the block should stay on screen for the viewer to
    comfortably read its content. Computed from the block's natural height
    so a 5-line bullet gets 5× the time of a 1-line bullet.
    """
    height = _block_height(block)
    if block.kind == "image":
        return max(2.0, height * _SEC_PER_IMAGE_UNIT_MIN)
    sec_per_line = _SEC_PER_LINE.get(block.kind, 1.5)
    return (height / BULLET_LINE_HEIGHT) * sec_per_line


def _narrate_blocks(blocks: list[Block], section_name: str) -> tuple[list[float], Path]:
    """Pre-narrate every block, then pad each block's wav with silence if
    Piper's output is shorter than the block needs to be on screen.

    Caching: Piper output is cached as ``block_NNN_raw.wav`` keyed by a
    sidecar ``block_NNN.txt`` containing the exact narration text. Re-narrate
    when the sidecar is missing (legacy cache) or its text differs from the
    current block — index-only caching mis-played stale audio for every
    block after a content shift (e.g., a new QUOTE block inserted upstream
    pushed every subsequent block's narration into the wrong slot). The
    padded version at ``block_NNN.wav`` is regenerated each run from the
    raw so the padding rate can be tuned without re-running Piper.

    Migration: any pre-existing ``block_NNN.wav`` that was created before
    this code split the two files is treated as the raw narration and
    renamed to ``block_NNN_raw.wav``. Avoids losing already-narrated wavs.
    """
    NARRATION_DIR.mkdir(parents=True, exist_ok=True)
    block_wavs_dir = NARRATION_DIR / section_name
    block_wavs_dir.mkdir(parents=True, exist_ok=True)

    durations: list[float] = []
    wav_paths_for_concat: list[Path] = []
    for i, b in enumerate(blocks):
        raw_wav = block_wavs_dir / f"block_{i:03d}_raw.wav"
        wav = block_wavs_dir / f"block_{i:03d}.wav"
        text_sidecar = block_wavs_dir / f"block_{i:03d}.txt"
        if b.has_audio:
            # Migrate any legacy unsplit wav → treat it as raw.
            if not raw_wav.exists() and wav.exists():
                wav.rename(raw_wav)
            current_text = b.narration_text
            cached_text = text_sidecar.read_text(encoding="utf-8") if text_sidecar.exists() else None
            if not raw_wav.exists() or cached_text != current_text:
                narrate_text(current_text, raw_wav)
                text_sidecar.write_text(current_text, encoding="utf-8")
            d_raw = get_audio_duration(raw_wav)
            min_view = _min_view_time(b)
            if d_raw + 0.05 < min_view:
                pad = min_view - d_raw
                silence = block_wavs_dir / f"block_{i:03d}_pad.wav"
                _make_silence_wav(pad, silence)
                concat_wavs([raw_wav, silence], wav)
                silence.unlink(missing_ok=True)
                d = min_view
            else:
                if wav.exists():
                    wav.unlink()
                wav.symlink_to(raw_wav.name)
                d = d_raw
        else:
            d = _synthesized_view_duration(b)
            if d <= 0:
                durations.append(0.0)
                continue
            silence_wav = block_wavs_dir / f"block_{i:03d}_silence.wav"
            if not silence_wav.exists():
                _make_silence_wav(d, silence_wav)
            wav = silence_wav
        durations.append(d)
        wav_paths_for_concat.append(wav)

    section_wav = NARRATION_DIR / f"{section_name}.wav"
    if wav_paths_for_concat:
        concat_wavs(wav_paths_for_concat, section_wav)
    return durations, section_wav


# --- Layout ---


def _layout(blocks: list[Block], durations: list[float]) -> tuple[list[dict], float, float]:
    """Stack blocks vertically using natural heights only. No duration-based
    padding — the camera handles narration timing on its own (see
    ``_camera_path``). Returns per-block layout dicts, total canvas height,
    and total audio duration.
    """
    layouts: list[dict] = []
    cursor_y = 0.0  # top edge of next block, decreasing into negative as we go down
    total_audio = 0.0
    for i, (b, d) in enumerate(zip(blocks, durations)):
        natural_h = _block_height(b)
        y_top = cursor_y
        y_center = y_top - natural_h / 2
        layouts.append({
            "kind": b.kind,
            "text": b.text,
            "display": b.display,
            "level": b.level,
            "resource_path": b.resource_path,
            "caption": b.caption,
            "attribution": b.attribution,
            "y_top": round(y_top, 3),
            "y_center": round(y_center, 3),
            "natural_height": round(natural_h, 3),
            "narration_duration": round(d, 3),
            "narration_start": round(total_audio, 3),
        })
        cursor_y = y_top - natural_h - INTER_BLOCK_PAD
        total_audio += d

    total_height = -cursor_y  # cursor is at the bottom edge after the last block
    return layouts, total_height, total_audio


# --- Camera animation ---


def _camera_path(layouts: list[dict], total_audio: float) -> list[dict]:
    """Build the camera y-curve via the unified scroll rule.

    For each block, during its narration window the camera moves linearly
    from "this block's top sits at viewport-y CAMERA_LEAD" to "the NEXT
    block's top sits at viewport-y CAMERA_LEAD." For the LAST block, the
    end target is just below it (so it scrolls fully through view).

    Distance traveled per block ≈ block height + INTER_BLOCK_PAD. With
    short blocks (bullets, ~0.5 units) and longer narrations (5-10s) the
    camera barely moves → looks like a settle. With tall blocks (images,
    ~6 units) and short view times (3-4s) the camera moves fast → looks
    like continuous flow. Same rule, both behaviors.
    """
    if not layouts:
        return []

    waypoints: list[dict] = []
    for i, layout in enumerate(layouts):
        t_start = layout["narration_start"]
        cam_start = round(layout["y_top"] + CAMERA_LEAD, 3)
        waypoints.append({"t": round(t_start, 3), "y": cam_start})

        if i + 1 < len(layouts):
            # End of this block: camera should have landed on the next block's
            # top so the trajectory is continuous (no snap between blocks).
            t_end = t_start + layout["narration_duration"]
            next_top = layouts[i + 1]["y_top"]
            cam_end = round(next_top + CAMERA_LEAD, 3)
            waypoints.append({"t": round(t_end, 3), "y": cam_end})
        else:
            # Last block: scroll past its bottom so the final words don't get
            # stuck at the top of the viewport with empty space below.
            t_end = t_start + layout["narration_duration"]
            block_bottom = layout["y_top"] - layout["natural_height"]
            cam_end = round(block_bottom + CAMERA_LEAD, 3)
            waypoints.append({"t": round(t_end, 3), "y": cam_end})

    return waypoints


# --- Write instructions + invoke manim + merge audio ---


def _write_instructions(
    section_name: str,
    layouts: list[dict],
    total_height: float,
    total_audio: float,
    camera_path: list[dict],
) -> Path:
    INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = INSTRUCTIONS_DIR / f"{section_name}.json"
    path.write_text(json.dumps({
        "section_name": section_name,
        "total_height": total_height,
        "total_duration": total_audio,
        "blocks": layouts,
        "camera_path": camera_path,
    }, indent=2), encoding="utf-8")
    logger.info(f"Wrote scroll instructions: {path}")
    return path


def _render_manim(instructions_path: Path, section_name: str) -> Path:
    """Invoke manim with ScrollScene. Output goes to the manim default
    location; we move/rename it to ``pipeline/scroll/output/``.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    silent_mp4 = OUTPUT_DIR / f"{section_name}_silent.mp4"

    env = os.environ.copy()
    env["SCROLL_INSTRUCTIONS_FILE"] = str(instructions_path.resolve())

    cmd = [
        MANIM_PYTHON, "-m", "manim", f"-{MANIM_QUALITY}",
        "src/scroll/manim_scene.py", "ScrollScene",
        "-o", silent_mp4.name,
        "--media_dir", "media/scroll",
    ]
    logger.info(f"Running manim: {' '.join(cmd)}")
    subprocess.run(cmd, env=env, check=True)

    # Manim writes to media/scroll/videos/manim_scene/<quality>/<scene_name>.mp4
    manim_output_root = Path("media/scroll/videos/manim_scene")
    if manim_output_root.exists():
        candidates = list(manim_output_root.rglob(silent_mp4.name))
        if candidates:
            candidates[0].rename(silent_mp4)
    return silent_mp4


def build_section(section_id: int) -> Path:
    section_name = f"section_{section_id}"
    cg_path = Path(GROUPING_CONTENT_GROUPS_DIR) / f"{section_name}.txt"
    if not cg_path.exists():
        logger.error(f"Missing content_groups: {cg_path}")
        sys.exit(1)

    groups = deserialize_groups(cg_path.read_text(encoding="utf-8"))
    blocks = _groups_to_blocks(groups, section_name)
    logger.info(f"Built {len(blocks)} blocks for {section_name}")

    durations, section_wav = _narrate_blocks(blocks, section_name)
    layouts, total_height, total_audio = _layout(blocks, durations)
    logger.info(
        f"Layout: total_height={total_height:.2f} units, "
        f"total_audio={total_audio:.2f}s"
    )

    camera_path = _camera_path(layouts, total_audio)
    instructions_path = _write_instructions(
        section_name, layouts, total_height, total_audio, camera_path
    )

    silent_mp4 = _render_manim(instructions_path, section_name)
    logger.info(f"Silent mp4: {silent_mp4}")

    # Merge silent video + narration directly so the output stays inside
    # ``pipeline/scroll/output/`` instead of the shared ``pipeline/output/``
    # the production pipeline uses. Keeps the prototype isolated.
    final_mp4 = OUTPUT_DIR / f"{section_name}.mp4"
    if final_mp4.exists():
        final_mp4.unlink()
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(silent_mp4),
        "-i", str(section_wav),
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(final_mp4),
    ], check=True, capture_output=True)
    logger.info(f"Final mp4: {final_mp4}")
    return final_mp4


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scroll-render a section (prototype)")
    parser.add_argument("section", type=int, help="Section number, e.g. 1")
    args = parser.parse_args()
    build_section(args.section)
