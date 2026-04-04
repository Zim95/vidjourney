"""
Timeline generator from scene groups.

Watches pipeline/groups/scene_groups/ for new files using watchdog.
When a scene group file arrives, parses it and generates one timeline file
per scene: timeline_<section_name>_scene_<number>.txt

For entity/relation scenes: scans narration left-to-right, spawns entities
in mention order, fires arrows when both ends are on screen, fades between groups.

For display scenes: spawns the resource, holds for narration duration, fades.
"""
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import dataclass, field

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

from src.config.constants import (
    GROUPING_SCENE_GROUPS_DIR,
    GROUPING_TIMELINES_DIR,
    GROUPING_WORDS_PER_MINUTE,
    GROUPING_MIN_SCENE_DURATION,
    GROUPING_SPAWN_GAP,
    GROUPING_ARROW_DURATION,
    GROUPING_FADE_DURATION,
    GROUPING_HOLD_AFTER_ARROW,
)

SCENE_GROUPS_DIR = GROUPING_SCENE_GROUPS_DIR
TIMELINES_DIR = GROUPING_TIMELINES_DIR

WORDS_PER_MINUTE = GROUPING_WORDS_PER_MINUTE
MIN_SCENE_DURATION = GROUPING_MIN_SCENE_DURATION
SPAWN_GAP = GROUPING_SPAWN_GAP
ARROW_DURATION = GROUPING_ARROW_DURATION
FADE_DURATION = GROUPING_FADE_DURATION
HOLD_AFTER_ARROW = GROUPING_HOLD_AFTER_ARROW


@dataclass
class TimelineEvent:
    time: float
    action: str         # SPAWN, ARROW, FADE, HOLD
    targets: list[str]
    duration: float


@dataclass
class SceneTimeline:
    scene_number: int
    narration: str
    display: str | None
    entities: list[str]
    total_duration: float
    events: list[TimelineEvent] = field(default_factory=list)


# --- Parsing ---

def _reading_time(text: str) -> float:
    words = len(text.split())
    seconds = (words / WORDS_PER_MINUTE) * 60.0
    return max(MIN_SCENE_DURATION, seconds)


def _find_entity_position(narration: str, entity: str) -> int:
    pos = narration.lower().find(entity.lower())
    return pos if pos >= 0 else len(narration)


def _parse_relation(raw: str) -> tuple[str, str, str] | None:
    match = re.match(r"(.+?)\s*--\s*(.+?)\s*-->\s*(.+)", raw.strip())
    if match:
        return (match.group(1).strip(), match.group(2).strip(), match.group(3).strip())
    return None


def _parse_scene_block(block: str) -> dict | None:
    scene_match = re.search(r"SCENE\s+(\d+)", block)
    if not scene_match:
        return None

    scene_number = int(scene_match.group(1))

    narrate_match = re.search(r"NARRATE:\s*(.+?)(?=\nDISPLAY:|\Z)", block, re.DOTALL)
    narration = narrate_match.group(1).strip() if narrate_match else ""

    display_match = re.search(r"DISPLAY:\s*(.*)", block)
    display = display_match.group(1).strip() if display_match else ""
    display = display if display else None

    entities_match = re.search(r"ENTITIES:\s*(.+?)(?=\nRELATIONS:|\Z)", block, re.DOTALL)
    entities_raw = entities_match.group(1).strip() if entities_match else ""
    entities = [e.strip() for e in entities_raw.split(",") if e.strip()] if entities_raw else []

    relations_match = re.search(r"RELATIONS:\s*(.+?)$", block, re.DOTALL)
    relations_raw = relations_match.group(1).strip() if relations_match else ""
    relations = []
    if relations_raw:
        for part in relations_raw.split(","):
            parsed = _parse_relation(part)
            if parsed:
                relations.append(parsed)

    return {
        "scene_number": scene_number,
        "narration": narration,
        "display": display,
        "entities": entities,
        "relations": relations,
    }


def parse_scene_group_file(filepath: Path) -> list[dict]:
    content = filepath.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"---+", content)
    scenes = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        parsed = _parse_scene_block(block)
        if parsed:
            scenes.append(parsed)
    return scenes


# --- Timeline building ---

def _group_entities_by_clause(narration: str, entities: list[str]) -> list[list[str]]:
    """Sort entities by mention position, group by clause boundaries."""
    positioned = []
    for entity in entities:
        pos = _find_entity_position(narration, entity)
        positioned.append((pos, entity))
    positioned.sort(key=lambda x: x[0])

    clause_breaks = [m.start() for m in re.finditer(r'[.;]\s+|\s+but\s+|\s+and\s+then\s+', narration)]

    groups: list[list[str]] = []
    current_group: list[str] = []
    current_break_idx = 0

    for pos, entity in positioned:
        while current_break_idx < len(clause_breaks) and clause_breaks[current_break_idx] < pos:
            if current_group:
                groups.append(current_group)
                current_group = []
            current_break_idx += 1
        current_group.append(entity)

    if current_group:
        groups.append(current_group)

    return groups if groups else [entities]


def _find_relations_for_group(
    group_entities: list[str],
    all_spawned: set[str],
    relations: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    spawned = all_spawned | {e.lower() for e in group_entities}
    matched = []
    for subject, verb, obj in relations:
        if subject.lower() in spawned and obj.lower() in spawned:
            matched.append((subject, verb, obj))
    return matched


def build_entity_timeline(parsed: dict) -> SceneTimeline:
    narration = parsed["narration"]
    entities = parsed["entities"]
    relations = parsed["relations"]
    total_duration = _reading_time(narration)

    groups = _group_entities_by_clause(narration, entities)
    events: list[TimelineEvent] = []
    current_time = 0.0
    all_spawned: set[str] = set()
    fired_relations: set[tuple[str, str, str]] = set()

    for group_idx, group in enumerate(groups):
        for entity in group:
            events.append(TimelineEvent(
                time=round(current_time, 2),
                action="SPAWN",
                targets=[entity],
                duration=SPAWN_GAP,
            ))
            current_time += SPAWN_GAP
            all_spawned.add(entity.lower())

        available = _find_relations_for_group(group, all_spawned, relations)
        for subject, verb, obj in available:
            rel_key = (subject, verb, obj)
            if rel_key in fired_relations:
                continue
            fired_relations.add(rel_key)
            events.append(TimelineEvent(
                time=round(current_time, 2),
                action="ARROW",
                targets=[subject, verb, obj],
                duration=ARROW_DURATION,
            ))
        if available:
            current_time += ARROW_DURATION

        events.append(TimelineEvent(
            time=round(current_time, 2),
            action="HOLD",
            targets=[],
            duration=HOLD_AFTER_ARROW,
        ))
        current_time += HOLD_AFTER_ARROW

        if group_idx < len(groups) - 1:
            events.append(TimelineEvent(
                time=round(current_time, 2),
                action="FADE",
                targets=list(group),
                duration=FADE_DURATION,
            ))
            current_time += FADE_DURATION

    events.append(TimelineEvent(
        time=round(current_time, 2),
        action="FADE",
        targets=["*"],
        duration=FADE_DURATION,
    ))

    return SceneTimeline(
        scene_number=parsed["scene_number"],
        narration=narration,
        display=None,
        entities=entities,
        total_duration=round(total_duration, 2),
        events=events,
    )


def build_display_timeline(parsed: dict) -> SceneTimeline:
    narration = parsed["narration"]
    display = parsed["display"]
    total_duration = _reading_time(narration)
    hold_time = max(1.0, total_duration - SPAWN_GAP - FADE_DURATION)

    events = [
        TimelineEvent(time=0.0, action="SPAWN", targets=[display], duration=SPAWN_GAP),
        TimelineEvent(time=round(SPAWN_GAP, 2), action="HOLD", targets=[display], duration=round(hold_time, 2)),
        TimelineEvent(time=round(SPAWN_GAP + hold_time, 2), action="FADE", targets=[display], duration=FADE_DURATION),
    ]

    return SceneTimeline(
        scene_number=parsed["scene_number"],
        narration=narration,
        display=display,
        entities=[],
        total_duration=round(total_duration, 2),
        events=events,
    )


# --- File writing ---

def _timeline_filename(section_name: str, scene_number: int) -> str:
    return f"timeline_{section_name}_scene_{scene_number}.txt"


def write_timeline(timeline: SceneTimeline, output_file: Path) -> None:
    lines = [
        f"SCENE {timeline.scene_number}",
        f"TOTAL_DURATION: {timeline.total_duration}s",
        f"NARRATE: {timeline.narration}",
    ]

    if timeline.display:
        lines.append(f"DISPLAY: {timeline.display}")

    if timeline.entities:
        lines.append(f"ENTITIES: {', '.join(timeline.entities)}")

    lines.append("")
    lines.append("TIMELINE:")
    for event in timeline.events:
        if event.action == "SPAWN":
            lines.append(f"  {event.time}s SPAWN {', '.join(event.targets)} ({event.duration}s)")
        elif event.action == "ARROW":
            subject, verb, obj = event.targets
            lines.append(f"  {event.time}s ARROW {subject} --[{verb}]--> {obj} ({event.duration}s)")
        elif event.action == "HOLD":
            lines.append(f"  {event.time}s HOLD ({event.duration}s)")
        elif event.action == "FADE":
            lines.append(f"  {event.time}s FADE {', '.join(event.targets)} ({event.duration}s)")

    output_file.write_text("\n".join(lines), encoding="utf-8")


# --- Process a single scene group file ---

def process_scene_group_file(filepath: Path) -> None:
    """Parse a scene group file and generate one timeline file per scene."""
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    section_name = filepath.stem  # e.g. "section_107"
    scenes = parse_scene_group_file(filepath)

    for parsed in scenes:
        filename = _timeline_filename(section_name, parsed["scene_number"])
        output_file = TIMELINES_DIR / filename

        if output_file.exists():
            continue

        if parsed["display"]:
            timeline = build_display_timeline(parsed)
        else:
            timeline = build_entity_timeline(parsed)

        write_timeline(timeline, output_file)
        print(f"    {filename}")


def process_scene_group_file_threaded(filepath: Path) -> None:
    """Parse a scene group file and generate timeline files concurrently."""
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    section_name = filepath.stem
    scenes = parse_scene_group_file(filepath)

    def _process_scene(parsed: dict) -> None:
        filename = _timeline_filename(section_name, parsed["scene_number"])
        output_file = TIMELINES_DIR / filename

        if output_file.exists():
            return

        if parsed["display"]:
            timeline = build_display_timeline(parsed)
        else:
            timeline = build_entity_timeline(parsed)

        write_timeline(timeline, output_file)
        print(f"    {filename}")

    with ThreadPoolExecutor() as executor:
        executor.map(_process_scene, scenes)


# --- Watchdog handler ---

class SceneGroupHandler(FileSystemEventHandler):
    """Watches scene_groups dir. On new file, generates timelines in a thread pool."""

    def __init__(self):
        self._executor = ThreadPoolExecutor()

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = Path(event.src_path)
        if filepath.suffix == ".txt" and filepath.stem.startswith("section_"):
            print(f"  TIMELINE: {filepath.name}")
            self._executor.submit(process_scene_group_file_threaded, filepath)


def start_watcher() -> Observer:
    """Start watching scene_groups directory for new files. Returns the observer."""
    SCENE_GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    handler = SceneGroupHandler()
    observer = Observer()
    observer.schedule(handler, str(SCENE_GROUPS_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer: Observer) -> None:
    observer.stop()
    observer.join()


