"""
Extract subject-verb-object relations from paragraph text using spaCy.
These map to visual elements in scene animations:
  - subjects/objects → shapes (blocks, nodes)
  - verbs → arrows/movements between shapes
"""

from dataclasses import dataclass

from src.scene_grouping.nlp import load_nlp

SUBJECT_DEPS = ("nsubj", "nsubjpass")
OBJECT_DEPS = ("dobj", "attr")


@dataclass
class Relation:
    subject: str
    verb: str
    object: str
    preposition: str | None = None  # e.g. "to server", "from disk"

    def __str__(self):
        prep = f" {self.preposition}" if self.preposition else ""
        return f"{self.subject} --[{self.verb}]--> {self.object}{prep}"


def _get_full_phrase(token) -> str:
    """Get the full noun phrase for a token by collecting its compound/amod children."""
    left_parts = [
        _get_full_phrase(child)
        for child in token.children
        if child.dep_ in ("compound", "amod", "poss") and child.i < token.i
    ]
    return " ".join(left_parts + [token.text])


def _get_prep_phrases(token) -> list[str]:
    """Get prepositional phrases attached to a verb."""
    return [
        f"{child.text} {_get_full_phrase(pobj)}"
        for child in token.children if child.dep_ == "prep"
        for pobj in child.children if pobj.dep_ == "pobj"
    ]


def _find_subjects(token) -> list:
    """Find subject tokens for a verb, including inherited from conjoined verbs."""
    subjects = [child for child in token.children if child.dep_ in SUBJECT_DEPS]
    if not subjects and token.dep_ == "conj" and token.head.pos_ == "VERB":
        subjects = [child for child in token.head.children if child.dep_ in SUBJECT_DEPS]
    return subjects


def _build_relation(subject_token, verb_token) -> Relation | None:
    """Build a Relation from a subject and verb token pair."""
    objects = [child for child in verb_token.children if child.dep_ in OBJECT_DEPS]
    prep_phrases = _get_prep_phrases(verb_token)

    subject_text = _get_full_phrase(subject_token)
    verb_text = verb_token.lemma_

    if objects:
        return Relation(
            subject=subject_text,
            verb=verb_text,
            object=_get_full_phrase(objects[0]),
            preposition=prep_phrases[0] if prep_phrases else None,
        )
    if prep_phrases:
        return Relation(
            subject=subject_text,
            verb=verb_text,
            object=prep_phrases[0],
        )
    return None


def extract_relations(text: str) -> list[Relation]:
    """
    Extract subject-verb-object relations from text.
    Each relation represents an action between entities.
    """
    nlp = load_nlp()
    doc = nlp(text)

    relations: list[Relation] = []
    for token in doc:
        if token.pos_ != "VERB":
            continue
        subjects = _find_subjects(token)
        if not subjects:
            continue
        relation = _build_relation(subjects[0], token)
        if relation:
            relations.append(relation)

    return relations


def extract_entities_and_actions(text: str) -> tuple[list[str], list[Relation]]:
    """
    Extract both unique entities (for shapes) and relations (for arrows) from text.
    Returns:
        entities: deduplicated list of entity names
        relations: list of Relation objects
    """
    relations = extract_relations(text)

    seen: set[str] = set()
    entities: list[str] = []

    for rel in relations:
        for name in (rel.subject, rel.object):
            lowered = name.lower()
            if lowered not in seen and len(name) > 1:
                seen.add(lowered)
                entities.append(name)

    return entities, relations
