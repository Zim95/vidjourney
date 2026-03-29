"""
Extract key noun phrases from paragraph text using spaCy.
These become visual elements (blocks, shapes, labels) in scene animations.
"""

from src.scene_grouping.nlp import load_nlp

GENERIC = {
    "example", "case", "way", "thing", "kind", "type", "number",
    "time", "order", "point", "fact", "result", "reason", "end",
    "part", "set", "lot", "rest", "use", "approach", "idea",
    "situation", "problem", "question", "answer", "detail",
}

SCORE_RULES = {
    "multi_word": lambda phrase, words: len(words) * 1.5,
    "has_capitalization": lambda phrase, words: 2.0 if any(c.isupper() for c in phrase[1:]) else 0.0,
}

SKIP_RULES = {
    "single_generic": lambda phrase, words: len(words) == 1 and words[0] in GENERIC,
    "too_short": lambda phrase, words: len(phrase) < 3,
    "too_long": lambda phrase, words: len(phrase) > 50,
}


def _strip_determiner(chunk) -> str:
    """Strip leading determiners from a noun chunk."""
    tokens = [t for t in chunk if not (t.pos_ == "DET" and t.i == chunk.start)]
    return " ".join(t.text for t in tokens).strip()


def extract_noun_phrases(text: str) -> list[str]:
    """
    Extract deduplicated noun phrases from text, stripped of leading determiners.
    Returns phrases sorted by order of first appearance.
    """
    nlp = load_nlp()
    doc = nlp(text)

    seen: set[str] = set()
    phrases: list[str] = []

    for chunk in doc.noun_chunks:
        clean = _strip_determiner(chunk)
        if not clean or len(clean) < 2:
            continue

        lowered = clean.lower()
        if lowered not in seen:
            seen.add(lowered)
            phrases.append(clean)

    return phrases


def extract_key_nouns(text: str, max_phrases: int = 8) -> list[str]:
    """
    Extract the most important noun phrases from text.
    Filters out generic/stopword-heavy phrases and limits count.
    """
    phrases = extract_noun_phrases(text)

    scored: list[tuple[float, str]] = []
    for phrase in phrases:
        words = phrase.lower().split()
        if any(rule(phrase, words) for rule in SKIP_RULES.values()):
            continue
        score = sum(rule(phrase, words) for rule in SCORE_RULES.values())
        scored.append((score, phrase))

    scored.sort(key=lambda x: -x[0])
    return [phrase for _score, phrase in scored[:max_phrases]]
