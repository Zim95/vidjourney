"""
Compile scene groups into .scene DSL files.

- narrate_resource scenes → display the resource image/code
- narrate_generated scenes → generate shapes from entities, arrows from relations
"""

import re
import json
from pathlib import Path

from src.scene_grouping.models import Scene
from src.scene_grouping.noun_extractor import extract_key_nouns
from src.scene_grouping.relation_extractor import extract_entities_and_actions, Relation
from src.compiler.layout import grid_layout, color_for_index
from src.compiler.duration import scene_durations


def _sanitize_name(text: str) -> str:
    """Convert a phrase to a valid DSL IDENT (alphanumeric + underscore)."""
    name = re.sub(r"[^A-Za-z0-9]+", "_", text.strip()).strip("_").lower()
    return name[:30] or "entity"


def _deduplicate_name(name: str, used: set[str]) -> str:
    """Ensure unique name by appending a counter if needed."""
    if name not in used:
        used.add(name)
        return name
    counter = 2
    while f"{name}_{counter}" in used:
        counter += 1
    unique = f"{name}_{counter}"
    used.add(unique)
    return unique


class SceneCompiler:

    @staticmethod
    def compile_resource_scene(scene: Scene, scene_index: int) -> str:
        """Compile a narrate_resource scene → DSL showing the resource."""
        combined_text = " ".join(scene.paragraphs + scene.list_items)
        dur = scene_durations(combined_text, num_arrows=0)

        lines = []
        resource = scene.resource
        element_name = f"resource_{scene_index}"

        element_type_map = {
            "IMAGE": lambda: _compile_image_element(element_name, resource.path, dur["spawn"]),
            "CODE_BLOCK": lambda: _compile_text_element(element_name, resource.caption or "Code", "rectangle", dur["spawn"]),
            "TABLE": lambda: _compile_text_element(element_name, resource.caption or "Table", "rectangle", dur["spawn"]),
            "DRAWING": lambda: _compile_text_element(element_name, resource.caption or "Drawing", "rectangle", dur["spawn"]),
        }

        compiler = element_type_map.get(resource.kind, lambda: _compile_text_element(element_name, "Resource", "rectangle", dur["spawn"]))
        lines.append(compiler())

        # sequence: spawn, display for body duration, remove
        lines.append("")
        lines.append("SEQUENCE")
        lines.append(f"    SPAWN {element_name}")
        lines.append(f"    WAIT {dur['body']}")
        lines.append(f"    CLOSE {element_name}")
        lines.append("END")

        return "\n".join(lines)

    @staticmethod
    def compile_generated_scene(scene: Scene, scene_index: int) -> str:
        """Compile a narrate_generated scene → DSL with shapes and arrows from NLP."""
        combined_text = " ".join(scene.paragraphs + scene.list_items)

        entities, relations = extract_entities_and_actions(combined_text)

        # fallback to key nouns if no relations found
        if not entities:
            entities = extract_key_nouns(combined_text, max_phrases=6)

        if not entities:
            label = scene.heading or "..."
            dur = scene_durations(combined_text, num_arrows=0)
            return _compile_title_scene(f"title_{scene_index}", label, dur["body"])

        # limit entities to keep layout clean
        entities = entities[:8]
        positions = grid_layout(len(entities))

        # count arrows first to calculate durations
        valid_relations = _filter_valid_relations(relations, entities)
        dur = scene_durations(combined_text, num_arrows=len(valid_relations))

        used_names: set[str] = set()
        entity_name_map: dict[str, str] = {}
        element_lines: list[str] = []
        spawn_targets: list[str] = []

        # compile entity shapes
        for idx, (entity, pos) in enumerate(zip(entities, positions)):
            name = _deduplicate_name(_sanitize_name(entity), used_names)
            entity_name_map[entity.lower()] = name

            element_lines.append(
                f'ELEMENT {name} TYPE shape\n'
                f'    TEXT "{entity}"\n'
                f'    POSITION ({pos[0]}, {pos[1]})\n'
                f'    SIZE 1.0\n'
                f'    SHAPE rectangle\n'
                f'    FILL {color_for_index(idx)}\n'
                f'    SPAWN popup {dur["spawn"]}\n'
                f'    REMOVE popout {dur["close"]}\n'
                f'END'
            )
            spawn_targets.append(name)

        # compile relation arrows
        arrow_names: list[str] = []

        for rel in valid_relations:
            subj_name = entity_name_map.get(rel.subject.lower())
            obj_name = entity_name_map.get(rel.object.lower())
            subj_idx = spawn_targets.index(subj_name)
            obj_idx = spawn_targets.index(obj_name)
            from_pos = positions[subj_idx]
            to_pos = positions[obj_idx]

            arrow_name = _deduplicate_name(f"arrow_{_sanitize_name(rel.verb)}", used_names)

            element_lines.append(
                f'ELEMENT {arrow_name} TYPE arrow\n'
                f'    TEXT "{rel.verb}"\n'
                f'    POSITION ({from_pos[0]}, {from_pos[1]})\n'
                f'    SIZE 1.0\n'
                f'    FILL white\n'
                f'    SPAWN popup 0.3\n'
                f'    MOVE straight TO ({to_pos[0]}, {to_pos[1]}) DURATION {dur["per_arrow"]}\n'
                f'    REMOVE popout 0.3\n'
                f'END'
            )
            arrow_names.append(arrow_name)

        # build sequence
        sequence_lines = ["SEQUENCE"]
        sequence_lines.append(f"    SPAWN {', '.join(spawn_targets)}")
        sequence_lines.append(f"    WAIT {dur['spawn']}")

        for arrow_name in arrow_names:
            sequence_lines.append(f"    SPAWN {arrow_name}")
            sequence_lines.append(f"    MOVE {arrow_name}")

        if not arrow_names:
            # no arrows — just hold the shapes for the body duration
            sequence_lines.append(f"    WAIT {dur['body']}")

        sequence_lines.append(f"    WAIT {dur['close']}")
        all_targets = spawn_targets + arrow_names
        sequence_lines.append(f"    CLOSE {', '.join(all_targets)}")
        sequence_lines.append("END")

        return "\n".join(element_lines) + "\n\n" + "\n".join(sequence_lines)

    @staticmethod
    def compile_scene(scene: Scene, scene_index: int) -> str:
        """Compile a single scene to DSL based on its type."""
        compilers = {
            "narrate_resource": SceneCompiler.compile_resource_scene,
            "narrate_generated": SceneCompiler.compile_generated_scene,
        }
        compiler = compilers.get(scene.scene_type, SceneCompiler.compile_generated_scene)
        return compiler(scene, scene_index)

    @staticmethod
    def compile_all(
        scene_groups_dir: Path = Path("pipeline") / "scene_groups",
        output_dir: Path = Path("pipeline") / "scenes",
    ) -> None:
        """Compile all scene group files into .scene DSL files."""
        output_dir.mkdir(parents=True, exist_ok=True)
        total = 0

        for filepath in sorted(scene_groups_dir.glob("section_*_scenes.txt")):
            section_num = int(filepath.stem.split("_")[1])
            scenes = _parse_scene_group_file(filepath)

            for i, scene in enumerate(scenes, 1):
                dsl = SceneCompiler.compile_scene(scene, i)
                out_path = output_dir / f"section_{section_num}_scene_{i}.scene"
                out_path.write_text(dsl + "\n", encoding="utf-8")
                total += 1

        print(f"Compiled {total} .scene files to {output_dir}")


# --- helpers ---

def _filter_valid_relations(relations: list[Relation], entities: list[str]) -> list[Relation]:
    """Keep only relations where both subject and object are in the entity list."""
    entity_set = {e.lower() for e in entities}
    return [
        rel for rel in relations
        if rel.subject.lower() in entity_set
        and rel.object.lower() in entity_set
        and rel.subject.lower() != rel.object.lower()
    ]


def _compile_image_element(name: str, path: str, spawn_time: float) -> str:
    return (
        f'ELEMENT {name} TYPE image\n'
        f'    URL "{path}"\n'
        f'    POSITION (0, 0)\n'
        f'    SIZE 4.0\n'
        f'    SPAWN popup {spawn_time}\n'
        f'    REMOVE popout 0.5\n'
        f'END'
    )


def _compile_text_element(name: str, label: str, shape: str, spawn_time: float) -> str:
    return (
        f'ELEMENT {name} TYPE shape\n'
        f'    TEXT "{label}"\n'
        f'    POSITION (0, 0)\n'
        f'    SIZE 2.0\n'
        f'    SHAPE {shape}\n'
        f'    FILL blue\n'
        f'    SPAWN popup {spawn_time}\n'
        f'    REMOVE popout 0.5\n'
        f'END'
    )


def _compile_title_scene(name: str, label: str, body_time: float) -> str:
    return (
        f'ELEMENT {name} TYPE shape\n'
        f'    TEXT "{label}"\n'
        f'    POSITION (0, 0)\n'
        f'    SIZE 2.0\n'
        f'    SHAPE rectangle\n'
        f'    FILL blue\n'
        f'    SPAWN popup 1.0\n'
        f'    REMOVE popout 0.5\n'
        f'END\n\n'
        f'SEQUENCE\n'
        f'    SPAWN {name}\n'
        f'    WAIT {body_time}\n'
        f'    CLOSE {name}\n'
        f'END'
    )


def _parse_scene_group_file(filepath: Path) -> list[Scene]:
    """Parse a scene group file back into Scene objects for compilation."""
    from src.scene_grouping.models import Resource

    scenes: list[Scene] = []
    current: dict | None = None

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")

            if line.startswith("scene:"):
                if current:
                    scenes.append(_dict_to_scene(current))
                current = {"paragraphs": [], "list_items": [], "resource": None}
                continue

            if current is None:
                continue

            field_parsers = {
                "type: ": lambda v: current.update(scene_type=v),
                "heading: ": lambda v: current.update(heading=v),
                "page_number: ": lambda v: current.update(page_number=int(v)),
                "PARAGRAPH ": lambda v: current["paragraphs"].append(v),
                "LIST_ITEM ": lambda v: current["list_items"].append(v),
                "CAPTION ": lambda v: (
                    current["resource"].__setattr__("caption", v) if current["resource"] else None
                ),
            }

            resource_types = {"IMAGE ", "CODE_BLOCK ", "TABLE ", "DRAWING "}

            handled = False
            for prefix, handler in field_parsers.items():
                if line.startswith(prefix):
                    handler(line[len(prefix):])
                    handled = True
                    break

            if not handled:
                matched_resource = False
                for rtype in resource_types:
                    if line.startswith(rtype):
                        kind = rtype.strip()
                        path = line[len(rtype):]
                        current["resource"] = Resource(kind=kind, path=path)
                        matched_resource = True
                        break

                # continuation line — append to last paragraph or list item
                if not matched_resource and line and not line.startswith(("NOUNS ", "ENTITIES ", "RELATIONS ", "section_number:")):
                    if current["paragraphs"]:
                        current["paragraphs"][-1] += "\n" + line
                    elif current["list_items"]:
                        current["list_items"][-1] += "\n" + line

    if current:
        scenes.append(_dict_to_scene(current))

    return scenes


def _dict_to_scene(d: dict) -> Scene:
    return Scene(
        paragraphs=d.get("paragraphs", []),
        list_items=d.get("list_items", []),
        resource=d.get("resource"),
        heading=d.get("heading"),
        page_number=d.get("page_number"),
        scene_type=d.get("scene_type", "narrate_generated"),
    )
