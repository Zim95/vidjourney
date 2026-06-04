"""
Deterministic quote-attribution detector.

A "quote" here is a paragraph that ends with an em/en-dash attribution that
includes a year in parens — e.g. ``—Alan Kay, ... (2012)``. That trailing
``(YYYY)`` is a near-zero-false-positive signal because mid-sentence em-dashes
in technical prose are almost always balanced (``X — clarification — Y``) and
don't end in a year-in-parens.

Used by:
- ``src.ingestion.section_detection.section_writer`` — emits ``QUOTE`` instead
  of ``PARAGRAPH`` at ingestion time so quote-shaped content gets a first-class
  marker, alongside HEADING / IMAGE / LIST_ITEM / etc.
- ``src.scene_grouping.llm_grouper`` — safety net for any quote that slipped
  through ingestion (older section files generated before this module existed,
  or paragraphs the PDF parser concatenated with the previous block).

Coverage: catches the year-bearing attributions in the corpus (Alan Kay,
Wittgenstein, Jay Kreps, etc.). Year-less attributions (e.g. bare ``—Donald
Knuth``) and non-Gregorian years (``360 BCE``, ``1265-1274``) are intentionally
out of scope — they still fall through to the LLM classifier downstream.
"""
import re


# Matches an em/en dash followed by a capitalized author and a "(YYYY)" year-
# in-parens source citation. The trailing year is what reliably marks the END
# of the attribution; without it we don't know where prose resumes, so we leave
# the paragraph alone rather than risk a false split.
#
# Two variants:
# - SPLIT: attribution followed by more prose in the same paragraph (the PDF
#   merged the quote with the next paragraph because there was no blank line
#   in the source).
# - TAIL: attribution at the very end of the paragraph (clean standalone
#   quote).
_ATTRIBUTION_BODY = r"[—–]\s*[A-Z][^()\n]{1,150}\([12]\d{3}\)[.,]?"

_QUOTE_ATTRIB_SPLIT_RE = re.compile(
    rf"({_ATTRIBUTION_BODY})\s+(?=[A-Z][a-z])"
)
_QUOTE_ATTRIB_TAIL_RE = re.compile(
    rf"{_ATTRIBUTION_BODY}\s*$"
)


def is_quote(text: str) -> bool:
    """True if ``text`` ends with a year-bearing em-dash attribution."""
    return bool(_QUOTE_ATTRIB_TAIL_RE.search(text.strip()))


def split_quote_and_prose(text: str) -> tuple[str, str] | None:
    """If ``text`` contains an attribution followed by more prose, return
    ``(quote_with_attribution, following_prose)``. Returns ``None`` if no
    split is needed — either because the text isn't a merged quote+prose
    block, or because the attribution is at the very end.

    Both halves are stripped; an empty second half collapses the result to
    ``None`` (caller should treat as standalone-quote case via ``is_quote``).
    """
    match = _QUOTE_ATTRIB_SPLIT_RE.search(text)
    if not match:
        return None
    split_at = match.end()
    head = text[:split_at].strip()
    tail = text[split_at:].strip()
    if not head or not tail:
        return None
    return head, tail
