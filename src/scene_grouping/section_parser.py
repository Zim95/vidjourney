"""Parse section files into structured element lists and resource indexes."""

from pathlib import Path

from src.scene_grouping.models import Resource, RESOURCE_TYPES, extract_identifiers

ELEMENT_TYPES = (
    "HEADING", "PARAGRAPH", "LIST_ITEM", "CAPTION",
    "CODE_BLOCK", "IMAGE", "TABLE", "DRAWING",
    "LINK", "ANNOTATION",
)


class SectionParser:

    @staticmethod
    def parse(filepath: Path) -> list[tuple[str, str]]:
        """Parse a section file into a list of (element_type, content) tuples."""
        elements: list[tuple[str, str]] = []

        line_handlers = {
            "section_number:": lambda line: None,  # skip
            "page_number:": lambda line: ("PAGE", line.split(":")[1].strip()),
        }

        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue

                # check prefix handlers
                handled = False
                for prefix, handler in line_handlers.items():
                    if line.startswith(prefix):
                        result = handler(line)
                        if result:
                            elements.append(result)
                        handled = True
                        break

                if handled:
                    continue

                # match element types
                matched_type = next(
                    (etype for etype in ELEMENT_TYPES if line.startswith(etype + " ") or line == etype),
                    None,
                )

                if matched_type:
                    content = line[len(matched_type):].strip()
                    elements.append((matched_type, content))
                elif elements:
                    # continuation line — append to previous element
                    last_type, last_content = elements[-1]
                    elements[-1] = (last_type, last_content + "\n" + line)

        return elements

    @staticmethod
    def build_resource_index(
        elements: list[tuple[str, str]],
    ) -> tuple[dict[str, Resource], dict[int, Resource]]:
        """
        Build maps for resource lookup:
          - identifier_to_resource: "figure 6-7" → Resource
          - position_resources: element_index → Resource
        """
        identifier_to_resource: dict[str, Resource] = {}
        position_resources: dict[int, Resource] = {}

        for i, (etype, content) in enumerate(elements):
            if etype not in RESOURCE_TYPES:
                continue

            resource = Resource(kind=etype, path=content)

            # look ahead for a caption
            if i + 1 < len(elements) and elements[i + 1][0] == "CAPTION":
                caption_text = elements[i + 1][1]
                resource.caption = caption_text
                ids = extract_identifiers(caption_text)
                if ids:
                    resource.identifier = next(iter(ids))
                    for ident in ids:
                        identifier_to_resource[ident.lower()] = resource

            position_resources[i] = resource

        return identifier_to_resource, position_resources
