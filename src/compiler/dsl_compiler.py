"""
DSL compiler: converts timeline files into .scene DSL files.

Unified approach: walks through timeline events and emits DSL.
Each SPAWN event becomes a shape ELEMENT. Each ARROW becomes an arrow ELEMENT.
SHOW_RESOURCE becomes an image ELEMENT. FADE becomes CLOSE. HOLD becomes WAIT.
"""
import re
from pathlib import Path

from src.config.constants import (
    GROUPING_TIMELINES_DIR,
    GROUPING_SCENE_FILES_DIR,
    GROUPING_CANVAS_X_MIN,
    GROUPING_CANVAS_X_MAX,
    GROUPING_CANVAS_Y_MIN,
    GROUPING_CANVAS_Y_MAX,
    GROUPING_GRID_MAX_COLS,
    GROUPING_SHAPE_SIZE,
    GROUPING_ANIMATION_SPAWN_TIME,
    GROUPING_ANIMATION_REMOVE_TIME,
)
from src.icons.icon_downloader import icon_path


TIMELINES_DIR = GROUPING_TIMELINES_DIR
SCENE_FILES_DIR = GROUPING_SCENE_FILES_DIR

CANVAS_X_MIN = GROUPING_CANVAS_X_MIN
CANVAS_X_MAX = GROUPING_CANVAS_X_MAX
CANVAS_Y_MIN = GROUPING_CANVAS_Y_MIN
CANVAS_Y_MAX = GROUPING_CANVAS_Y_MAX
GRID_MAX_COLS = GROUPING_GRID_MAX_COLS
SHAPE_SIZE = GROUPING_SHAPE_SIZE
SPAWN_TIME = GROUPING_ANIMATION_SPAWN_TIME
REMOVE_TIME = GROUPING_ANIMATION_REMOVE_TIME

COLORS = ["blue", "green", "red", "orange", "purple", "yellow", "teal", "pink"]


# --- Utilities ---

def _sanitize_ident(name: str, suffix: int = 0) -> str:
    ident = re.sub(r"[^A-Za-z0-9_]", "_", name)
    ident = re.sub(r"_+", "_", ident).strip("_")
    if not ident or ident[0].isdigit():
        ident = "e_" + ident
    ident = ident.lower()[:25]
    if suffix:
        ident = f"{ident}_{suffix}"
    return ident


def _grid_positions(count: int) -> list[tuple[float, float]]:
    if count == 0:
        return []
    if count == 1:
        return [(0.0, 0.0)]

    cols = min(count, GRID_MAX_COLS)
    rows = (count + cols - 1) // cols
    x_step = (CANVAS_X_MAX - CANVAS_X_MIN) / (cols + 1)
    y_step = (CANVAS_Y_MAX - CANVAS_Y_MIN) / (rows + 1)

    positions = []
    for i in range(count):
        row = i // cols
        col = i % cols
        x = round(CANVAS_X_MIN + (col + 1) * x_step, 1)
        y = round(CANVAS_Y_MAX - (row + 1) * y_step, 1)
        positions.append((x, y))
    return positions


def _escape_dsl_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


# --- Timeline parsing ---

def _parse_timeline_file(filepath: Path) -> dict:
    content = filepath.read_text(encoding="utf-8", errors="replace")

    scene_match = re.search(r"SCENE\s+(\d+)", content)
    scene_number = int(scene_match.group(1)) if scene_match else 0

    duration_match = re.search(r"TOTAL_DURATION:\s*([\d.]+)s", content)
    total_duration = float(duration_match.group(1)) if duration_match else 4.0

    voiceover_match = re.search(r'VOICEOVER:\s*"?(.+?)"?\s*$', content, re.MULTILINE)
    voiceover = voiceover_match.group(1).strip().strip('"') if voiceover_match else ""

    events = []
    for line in content.split("\n"):
        line = line.strip()
        if not re.match(r"[\d.]+s\s+", line):
            continue

        match = re.match(r'([\d.]+)s\s+(\w+)(?:\s+"(.*?)")?\s*\(([\d.]+)s\)', line)
        if match:
            events.append({
                "time": float(match.group(1)),
                "action": match.group(2),
                "target": (match.group(3) or "").strip(),
                "duration": float(match.group(4)),
            })

    return {
        "scene_number": scene_number,
        "total_duration": total_duration,
        "voiceover": voiceover,
        "events": events,
    }


# --- DSL generation ---

def _compile_events(events: list[dict]) -> str:
    """
    Walk through timeline events and emit DSL.
    Handles SPAWN, ARROW, SHOW_RESOURCE, SHOW_TEXT, FADE, HOLD.
    """
    elements: list[str] = []
    sequence: list[str] = []

    # Track SPAWN events to assign positions/idents/colors
    spawn_targets: list[str] = []
    ident_by_target: dict[tuple[str, int], str] = {}  # (target, occurrence) -> ident
    ident_by_index: list[str] = []  # indexed by SPAWN event order
    target_occurrences: dict[str, int] = {}
    color_idx = 0
    arrow_count = 0

    # First pass: count spawns to determine grid positions
    spawn_events = [e for e in events if e["action"] == "SPAWN"]
    positions = _grid_positions(len(spawn_events))

    # Walk events in time order
    sorted_events = sorted(events, key=lambda e: e["time"])
    prev_time = 0.0

    for event in sorted_events:
        gap = round(event["time"] - prev_time, 1)
        if gap > 0 and sequence:
            sequence.append(f'    WAIT {gap}')

        if event["action"] == "SPAWN":
            target = event["target"]
            occ = target_occurrences.get(target, 0)
            target_occurrences[target] = occ + 1

            spawn_idx = len(ident_by_index)
            ident = _sanitize_ident(target, suffix=spawn_idx)
            ident_by_target[(target, occ)] = ident
            ident_by_index.append(ident)

            x, y = positions[spawn_idx] if spawn_idx < len(positions) else (0.0, 0.0)
            color = COLORS[color_idx % len(COLORS)]
            color_idx += 1

            # check if an icon SVG exists for this entity
            svg_path = icon_path(target)
            if svg_path.exists():
                elements.extend([
                    f'ELEMENT {ident} TYPE image',
                    f'    URL "{_escape_dsl_string(str(svg_path))}"',
                    f'    TEXT "{_escape_dsl_string(target)}"',
                    f'    POSITION ({x},{y})',
                    f'    SIZE {SHAPE_SIZE}',
                    f'    SPAWN image_popup {SPAWN_TIME}',
                    f'    REMOVE image_popout {REMOVE_TIME}',
                    f'END',
                    f'',
                ])
            else:
                elements.extend([
                    f'ELEMENT {ident} TYPE shape',
                    f'    SHAPE auto_rect',
                    f'    TEXT "{_escape_dsl_string(target)}"',
                    f'    POSITION ({x},{y})',
                    f'    SIZE {SHAPE_SIZE}',
                    f'    FILL {color}',
                    f'    SPAWN shape_popup {SPAWN_TIME}',
                    f'    REMOVE shape_popout {REMOVE_TIME}',
                    f'END',
                    f'',
                ])
            sequence.append(f'    SPAWN {ident}')
            prev_time = event["time"]

        elif event["action"] == "ARROW":
            # target format: "subject --[verb]--> object"
            target = event["target"]
            match = re.match(r"(.+?)\s*--\[(.+?)\]-->\s*(.+)", target)
            if not match:
                prev_time = event["time"]
                continue

            subject, verb, obj = match.group(1).strip(), match.group(2).strip(), match.group(3).strip()

            # find idents for subject and object — use most recent occurrences
            subj_ident = None
            obj_ident = None
            for (t, _), ident in reversed(list(ident_by_target.items())):
                if subj_ident is None and t == subject:
                    subj_ident = ident
                if obj_ident is None and t == obj:
                    obj_ident = ident
                if subj_ident and obj_ident:
                    break

            if not subj_ident or not obj_ident:
                prev_time = event["time"]
                continue

            arrow_count += 1
            arrow_ident = f"arrow_{arrow_count}"

            # find positions
            subj_spawn_idx = ident_by_index.index(subj_ident)
            obj_spawn_idx = ident_by_index.index(obj_ident)
            subj_pos = positions[subj_spawn_idx]
            obj_pos = positions[obj_spawn_idx]

            # offset to shape edges
            shape_half = SHAPE_SIZE / 2
            dx = obj_pos[0] - subj_pos[0]
            dy = obj_pos[1] - subj_pos[1]
            dist = max((dx ** 2 + dy ** 2) ** 0.5, 0.01)
            nx, ny = dx / dist, dy / dist
            start = (round(subj_pos[0] + nx * shape_half, 1), round(subj_pos[1] + ny * shape_half, 1))
            end = (round(obj_pos[0] - nx * shape_half, 1), round(obj_pos[1] - ny * shape_half, 1))

            duration = event["duration"]

            elements.extend([
                f'ELEMENT {arrow_ident} TYPE arrow',
                f'    TEXT "{_escape_dsl_string(verb)}"',
                f'    POSITION ({start[0]},{start[1]})',
                f'    SPAWN unidirectional_dotted_spawn {duration}',
                f'    MOVE straight TO ({end[0]},{end[1]}) DURATION {duration}',
                f'    REMOVE unidirectional_dotted_remove {REMOVE_TIME}',
                f'END',
                f'',
            ])
            sequence.append(f'    SPAWN {arrow_ident}')
            sequence.append(f'    MOVE {arrow_ident}')
            prev_time = event["time"]

        elif event["action"] == "SHOW_RESOURCE":
            ident = "resource"
            # target may include caption: "path|||caption"
            raw_target = event["target"]
            if "|||" in raw_target:
                path, caption = raw_target.split("|||", 1)
            else:
                path, caption = raw_target, ""
            element_lines = [
                f'ELEMENT {ident} TYPE image',
                f'    URL "{_escape_dsl_string(path)}"',
                f'    POSITION (0.0,0.0)',
                f'    SIZE 8.0',
            ]
            if caption:
                element_lines.append(f'    TEXT "{_escape_dsl_string(caption)}"')
            element_lines.extend([
                f'    SPAWN image_popup {SPAWN_TIME}',
                f'    REMOVE image_popout {REMOVE_TIME}',
                f'END',
                f'',
            ])
            elements.extend(element_lines)
            sequence.append(f'    SPAWN {ident}')
            ident_by_index.append(ident)
            prev_time = event["time"]

        elif event["action"] == "SHOW_HEADING":
            heading_idx = len([i for i in ident_by_index if i.startswith("heading_")])
            ident = f"heading_{heading_idx}"
            elements.extend([
                f'ELEMENT {ident} TYPE shape',
                f'    SHAPE text_heading',
                f'    TEXT "{_escape_dsl_string(event["target"])}"',
                f'    POSITION (0.0,0.0)',
                f'    SIZE 10.0',
                f'    SPAWN shape_popup {SPAWN_TIME}',
                f'    REMOVE shape_popout {REMOVE_TIME}',
                f'END',
                f'',
            ])
            sequence.append(f'    SPAWN {ident}')
            ident_by_index.append(ident)
            prev_time = event["time"]

        elif event["action"] == "SHOW_QUOTE":
            quote_idx = len([i for i in ident_by_index if i.startswith("quote_")])
            ident = f"quote_{quote_idx}"
            elements.extend([
                f'ELEMENT {ident} TYPE shape',
                f'    SHAPE text_quote',
                f'    TEXT "{_escape_dsl_string(event["target"])}"',
                f'    POSITION (0.0,0.0)',
                f'    SIZE 10.0',
                f'    SPAWN shape_popup {SPAWN_TIME}',
                f'    REMOVE shape_popout {REMOVE_TIME}',
                f'END',
                f'',
            ])
            sequence.append(f'    SPAWN {ident}')
            ident_by_index.append(ident)
            prev_time = event["time"]

        elif event["action"] == "SHOW_TEXT":
            text_idx = len([i for i in ident_by_index if i.startswith("text_")])
            ident = f"text_{text_idx}"
            # position below image if resource exists, otherwise center
            y = -2.5 if "resource" in ident_by_index else 0.0
            elements.extend([
                f'ELEMENT {ident} TYPE shape',
                f'    SHAPE rectangle',
                f'    TEXT "{_escape_dsl_string(event["target"])}"',
                f'    POSITION (0.0,{y})',
                f'    SIZE 1.0',
                f'    FILL green',
                f'    SPAWN shape_popup {SPAWN_TIME}',
                f'    REMOVE shape_popout {REMOVE_TIME}',
                f'END',
                f'',
            ])
            sequence.append(f'    SPAWN {ident}')
            ident_by_index.append(ident)
            prev_time = event["time"]

        elif event["action"] == "FADE":
            if event["target"] == "*":
                # close everything spawned so far that hasn't been closed
                if ident_by_index:
                    sequence.append(f'    CLOSE {", ".join(ident_by_index)}')
                    ident_by_index.clear()
                    ident_by_target.clear()
                    target_occurrences.clear()
            prev_time = event["time"]

        elif event["action"] == "HOLD":
            # Emit an explicit WAIT for the hold duration so Manim actually renders
            # frames for this scene (blank screen). Gap-based WAITs only fire between
            # events, so a HOLD-only scene would otherwise produce an empty sequence.
            hold_duration = round(float(event["duration"]), 1)
            if hold_duration > 0:
                sequence.append(f'    WAIT {hold_duration}')
            prev_time = event["time"] + event["duration"]

    if not sequence:
        return ""

    return "\n".join(elements + ["SEQUENCE"] + sequence + ["END"])


def compile_timeline(filepath: Path) -> str:
    parsed = _parse_timeline_file(filepath)
    return _compile_events(parsed["events"])


# --- File processing ---

def process_timeline_file(filepath: Path) -> None:
    SCENE_FILES_DIR.mkdir(parents=True, exist_ok=True)
    output_file = SCENE_FILES_DIR / f"{filepath.stem}.scene"
    if output_file.exists():
        return
    dsl = compile_timeline(filepath)
    if not dsl.strip():
        return
    output_file.write_text(dsl, encoding="utf-8")
    print(f"    {output_file.name}")


def process_all_timelines() -> None:
    SCENE_FILES_DIR.mkdir(parents=True, exist_ok=True)
    timeline_files = sorted(TIMELINES_DIR.glob("timeline_*.txt"))
    compiled = 0
    for f in timeline_files:
        output_file = SCENE_FILES_DIR / f"{f.stem}.scene"
        if output_file.exists():
            continue
        try:
            dsl = compile_timeline(f)
            if not dsl.strip():
                continue
            output_file.write_text(dsl, encoding="utf-8")
            compiled += 1
        except Exception as e:
            print(f"  FAILED: {f.name} — {e}")
    print(f"Compiled {compiled} scene file(s).")


# --- Watchdog ---

def start_watcher(executor=None):
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    from concurrent.futures import ThreadPoolExecutor

    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)
    SCENE_FILES_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()

    class TimelineHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            filepath = Path(event.src_path)
            if filepath.suffix == ".txt" and filepath.stem.startswith("timeline_"):
                _executor.submit(process_timeline_file, filepath)

    handler = TimelineHandler()
    observer = Observer()
    observer.schedule(handler, str(TIMELINES_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer) -> None:
    observer.stop()
    observer.join()
