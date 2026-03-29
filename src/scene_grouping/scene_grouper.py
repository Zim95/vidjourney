"""
Scene Grouper: every scene has a paragraph (narration) and optionally a resource (visual).

- narrate_resource: paragraph + existing resource (image/code/table) → display resource on screen
- narrate_generated: paragraph + no resource → generate visuals from nouns/relations

Association rules (in priority order):
1. Caption match — paragraph mentions "Figure X-Y" and a resource has that caption.
2. Adjacency — paragraph is directly before/after a resource.
3. No match — standalone, visuals need to be generated.
"""

from pathlib import Path

from src.scene_grouping.models import Resource, Scene, RESOURCE_TYPES, extract_identifiers
from src.scene_grouping.section_parser import SectionParser


class _GroupingState:
    """Mutable state passed through element handlers during grouping."""

    def __init__(self, elements, identifier_to_resource, position_resources):
        self.elements = elements
        self.identifier_to_resource = identifier_to_resource
        self.position_resources = position_resources
        self.scenes: list[Scene] = []
        self.current_page: int | None = None
        self.current_heading: str | None = None
        self.i: int = 0

    def _skip_caption(self):
        if self.i < len(self.elements) and self.elements[self.i][0] == "CAPTION":
            self.i += 1

    def _make_scene(self, **kwargs) -> Scene:
        defaults = {"heading": self.current_heading, "page_number": self.current_page}
        defaults.update(kwargs)
        return Scene(**defaults)

    # --- element handlers ---

    def handle_page(self, content: str):
        self.current_page = int(content)
        self.i += 1

    def handle_heading(self, content: str):
        self.current_heading = content
        self.i += 1

    def handle_caption(self, _content: str):
        self.i += 1

    def handle_resource(self, _content: str):
        # orphan resource with no paragraph — skip, not a scene
        self.i += 1
        self._skip_caption()

    def handle_text(self, _content: str):
        # collect consecutive paragraphs and list items
        text_elements: list[tuple[str, str]] = []
        while self.i < len(self.elements) and self.elements[self.i][0] in ("PARAGRAPH", "LIST_ITEM"):
            text_elements.append(self.elements[self.i])
            self.i += 1

        paragraphs = [c for t, c in text_elements if t == "PARAGRAPH"]
        list_items = [c for t, c in text_elements if t == "LIST_ITEM"]
        combined_text = " ".join(paragraphs + list_items)

        # association strategies in priority order
        strategies = [
            lambda: self._match_by_caption(combined_text),
            lambda: self._match_by_adjacency(),
        ]

        for strategy in strategies:
            resource = strategy()
            if resource:
                self.scenes.append(self._make_scene(
                    paragraphs=paragraphs,
                    list_items=list_items,
                    resource=resource,
                    scene_type="narrate_resource",
                ))
                # skip the resource + caption if they follow
                if self.i < len(self.elements) and self.elements[self.i][0] in RESOURCE_TYPES:
                    self.i += 1
                    self._skip_caption()
                return

        # no resource matched — visuals need to be generated
        self.scenes.append(self._make_scene(
            paragraphs=paragraphs,
            list_items=list_items,
            scene_type="narrate_generated",
        ))

    def _match_by_caption(self, combined_text: str) -> Resource | None:
        referenced_ids = extract_identifiers(combined_text)
        for ref_id in referenced_ids:
            resource = self.identifier_to_resource.get(ref_id.lower())
            if resource:
                return resource
        return None

    def _match_by_adjacency(self) -> Resource | None:
        if self.i < len(self.elements) and self.elements[self.i][0] in RESOURCE_TYPES:
            return self.position_resources.get(self.i)
        return None

    def handle_skip(self, _content: str):
        self.i += 1


# dispatch table: element_type → handler method name
_ELEMENT_HANDLERS = {
    "PAGE": "handle_page",
    "HEADING": "handle_heading",
    "CAPTION": "handle_caption",
    "CODE_BLOCK": "handle_resource",
    "IMAGE": "handle_resource",
    "TABLE": "handle_resource",
    "DRAWING": "handle_resource",
    "PARAGRAPH": "handle_text",
    "LIST_ITEM": "handle_text",
}


class SceneGrouper:

    @staticmethod
    def group_section(filepath: Path) -> list[Scene]:
        """Group a section file into scenes."""
        elements = SectionParser.parse(filepath)
        if not elements:
            return []

        identifier_to_resource, position_resources = SectionParser.build_resource_index(elements)
        state = _GroupingState(elements, identifier_to_resource, position_resources)

        while state.i < len(elements):
            etype, content = elements[state.i]
            handler_name = _ELEMENT_HANDLERS.get(etype, "handle_skip")
            getattr(state, handler_name)(content)

        return state.scenes

    @staticmethod
    def group_all_sections(sections_dir: Path) -> dict[int, list[Scene]]:
        """Group all section files in a directory into scenes."""
        return {
            int(filepath.stem.split("_")[1]): SceneGrouper.group_section(filepath)
            for filepath in sorted(sections_dir.glob("section_*.txt"))
        }

    @staticmethod
    def summary(scenes: list[Scene]) -> None:
        """Print a summary of scenes for debugging."""
        for i, scene in enumerate(scenes, 1):
            resource_info = f"{scene.resource.kind} ({scene.resource.identifier or scene.resource.path})" if scene.resource else "NONE"
            print(f"  Scene {i}: type={scene.scene_type}, paragraphs={len(scene.paragraphs)}, list_items={len(scene.list_items)}, resource={resource_info}")
