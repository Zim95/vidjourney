"""
Group pipeline: takes section files and produces scene groups with extracted
nouns and relations for each scene.

Called after ingestion writes section files.
"""

import json
from pathlib import Path

from src.scene_grouping.scene_grouper import SceneGrouper
from src.scene_grouping.noun_extractor import extract_key_nouns
from src.scene_grouping.relation_extractor import extract_entities_and_actions


def group(
    sections_dir: Path = Path("pipeline") / "sections",
    output_dir: Path = Path("pipeline") / "scene_groups",
) -> None:
    """
    Run the full grouping pipeline:
    1. Group sections into scenes (paragraph + resource associations)
    2. Extract nouns and relations for each standalone scene
    3. Write scene group files with extracted NLP data
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    all_scenes = SceneGrouper.group_all_sections(sections_dir)
    total_scenes = 0
    standalone_count = 0

    for section_num, scenes in all_scenes.items():
        lines: list[str] = [f"section_number: {section_num}", ""]

        for i, scene in enumerate(scenes, 1):
            lines.append(f"scene: {i}")
            lines.append(f"type: {scene.scene_type}")
            if scene.heading:
                lines.append(f"heading: {scene.heading}")
            if scene.page_number:
                lines.append(f"page_number: {scene.page_number}")

            for para in scene.paragraphs:
                lines.append(f"PARAGRAPH {para}")
            for item in scene.list_items:
                lines.append(f"LIST_ITEM {item}")

            if scene.resource:
                lines.append(f"{scene.resource.kind} {scene.resource.path}")
                if scene.resource.caption:
                    lines.append(f"CAPTION {scene.resource.caption}")

            # extract nouns and relations for standalone scenes
            if scene.scene_type == "narrate_generated" and scene.paragraphs:
                combined_text = " ".join(scene.paragraphs)
                nouns = extract_key_nouns(combined_text)
                entities, relations = extract_entities_and_actions(combined_text)

                if nouns:
                    lines.append(f"NOUNS {json.dumps(nouns)}")
                if entities:
                    lines.append(f"ENTITIES {json.dumps(entities)}")
                if relations:
                    lines.append(f"RELATIONS {json.dumps([str(r) for r in relations])}")

                standalone_count += 1

            lines.append("")
            total_scenes += 1

        filepath = output_dir / f"section_{section_num}_scenes.txt"
        filepath.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    print(f"Wrote {total_scenes} scenes ({standalone_count} standalone with NLP) across {len(all_scenes)} sections to {output_dir}")
