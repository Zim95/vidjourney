"""
Data model for the scroll-rendering prototype.

A section's content is a flat sequence of ``Block`` objects laid out on a
single vertical y-axis. There are no "scenes" or "pages" — the camera
scrolls down through the section from top to bottom while narration plays.

Block kinds:
- ``heading``   — section / part heading, large centered text
- ``paragraph`` — narrated prose with no on-screen list bullet (the audio
                  plays while the camera holds; useful as a transition or
                  if the content doesn't fit a bullet pattern)
- ``bullet``    — list bullet at a given nesting level (0, 1, 2)
- ``image``     — figure / code block shown inline at moderate size
- ``quote``     — centered quoted text with attribution

Each block carries:
- ``text``      — the audio narration text (Piper TTS reads this verbatim)
- ``display``   — what shows on screen. For bullets, this is the short
                  summary; for paragraph blocks, this can be empty
                  (narration-only) or a longer chunk to display alongside
- ``level``     — nesting depth for bullets (0/1/2); ignored otherwise
- ``resource_path`` — file path for image blocks
- ``caption``   — caption shown below image blocks
- ``attribution`` — attribution shown below quote blocks
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Block:
    kind: str
    text: str
    display: str = ""
    level: int = 0
    resource_path: str | None = None
    caption: str = ""
    attribution: str = ""

    @property
    def has_audio(self) -> bool:
        """True if this block contributes narration to the scene wav.

        Headings + bullets + paragraphs + quotes all narrate. Images
        narrate their caption (if any) so the camera lingers on them
        long enough to read.
        """
        return bool(self.text.strip()) or bool(self.caption.strip())

    @property
    def narration_text(self) -> str:
        """Text actually narrated. Falls back to caption for image blocks
        when no explicit text was set."""
        if self.text.strip():
            return self.text.strip()
        return self.caption.strip()
