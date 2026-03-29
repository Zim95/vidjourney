"""Shared spaCy model loader for all NLP modules."""

import spacy

_nlp = None


def load_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_sm")
    return _nlp
