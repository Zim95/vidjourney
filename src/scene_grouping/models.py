"""Data models for scene grouping."""

import re
from dataclasses import dataclass, field

RESOURCE_TYPES = {"CODE_BLOCK", "IMAGE", "TABLE", "DRAWING"}

IDENTIFIER_RE = re.compile(r"(Figure|Table|Example)\s+(\d+-\d+)", re.IGNORECASE)


@dataclass
class Resource:
    kind: str  # "IMAGE", "CODE_BLOCK", "TABLE", "DRAWING"
    path: str
    caption: str | None = None
    identifier: str | None = None  # e.g. "Figure 6-7"


@dataclass
class Scene:
    paragraphs: list[str] = field(default_factory=list)
    list_items: list[str] = field(default_factory=list)
    resource: Resource | None = None
    heading: str | None = None
    page_number: int | None = None
    scene_type: str = "narrate_generated"  # "narrate_resource" or "narrate_generated"


def extract_identifiers(text: str) -> set[str]:
    """Extract all figure/table/example identifiers from text."""
    return {
        f"{m.group(1)} {m.group(2)}"
        for m in IDENTIFIER_RE.finditer(text)
    }
