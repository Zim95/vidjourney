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
- ``src.grouping.llm_grouper`` — safety net for any quote that slipped
  through ingestion (older section files generated before this module existed,
  or paragraphs the PDF parser concatenated with the previous block).

Coverage: ``is_quote`` catches the year-bearing attributions in the corpus
(Alan Kay, Wittgenstein, Jay Kreps, etc.) with near-zero false positives, and
is the strict signal used at ingestion time. Year-less attributions (bare
``—Donald Knuth``) and non-Gregorian years (``360 BCE``, ``1265-1274``) are
handled by the lower-precision ``has_quote_attribution`` /
``split_quote_body_and_attribution`` helpers, used only at render time (they
replaced the old Ollama quote LLM). Genuinely ambiguous cases are deferred to
the Phase-6 review gate rather than guessed at.
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


# Broader attribution tail: em/en-dash + a capitalized author name at the very
# END of the paragraph, optionally followed by a ", source/year" continuation.
# Catches the year-less ("—Donald Knuth") and non-Gregorian-year ("—Author,
# 360 BCE") attributions the strict year-in-parens pattern above skips. Lower
# precision — a paragraph that merely ends on a capitalized word after a dash
# can trip it — so it is used ONLY at render time, never to auto-tag at
# ingestion. Requires a capitalized word right after the dash, which rules out
# the common balanced mid-prose em-dash ("X — really — Y").
_ATTRIB_TAIL_BROAD_RE = re.compile(
    r"[—–]\s*[A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+)*(?:,[^—–\n]{0,150})?\s*$"
)


def has_quote_attribution(text: str) -> bool:
    """True if ``text`` ends with an em-dash attribution — either the
    high-confidence year-bearing form (as ``is_quote``) or the broader
    bare-name / non-Gregorian-year form. Used at render time to catch quotes
    the strict ingestion detector skipped; broader and lower-precision than
    ``is_quote``.
    """
    stripped = text.strip()
    return bool(
        _QUOTE_ATTRIB_TAIL_RE.search(stripped)
        or _ATTRIB_TAIL_BROAD_RE.search(stripped)
    )


def split_quote_body_and_attribution(text: str) -> tuple[str, str]:
    """Split a quote paragraph into ``(body, attribution)`` for display.

    Deterministic replacement for the retired LLM ``extract_quote``. Finds the
    em-dash attribution tail (year-bearing first, then the broader bare-name
    form), returning the text before the dash as the quote body and the text
    after (dash + surrounding quote marks stripped) as the attribution. If no
    attribution tail is found, returns ``(text, "")`` — the whole paragraph is
    the body — matching the old LLM fallback so a quote still renders.
    """
    stripped = text.strip()
    for pattern in (_QUOTE_ATTRIB_TAIL_RE, _ATTRIB_TAIL_BROAD_RE):
        match = pattern.search(stripped)
        if not match:
            continue
        dash_start = match.start()
        body = stripped[:dash_start].strip().strip('"“”').strip()
        attribution = stripped[dash_start:].lstrip("—–").strip().strip('"“”').strip()
        if body:
            return body, attribution
    return stripped.strip('"“”').strip(), ""
