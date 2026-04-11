"""
DSL compiler: converts timeline files into .scene DSL files.

Timeline events map to DSL:
  SHOW_TEXT    → ELEMENT TYPE shape with TEXT, SPAWN/WAIT/CLOSE in SEQUENCE
  SHOW_LIST    → Multiple ELEMENT TYPE shape for each item, SPAWN all in SEQUENCE
  HIGHLIGHT    → (no-op in DSL — item is already visible, could add color change later)
  SPAWN        → ELEMENT TYPE shape with TEXT, SPAWN in SEQUENCE
  ARROW        → ELEMENT TYPE arrow, SPAWN + MOVE in SEQUENCE
  SHOW_RESOURCE → ELEMENT TYPE image with URL, SPAWN in SEQUENCE
  HOLD         → WAIT in SEQUENCE
  FADE         → CLOSE in SEQUENCE
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
    GROUPING_LIST_X_POSITION,
    GROUPING_LIST_Y_START,
    GROUPING_LIST_Y_STEP,
    GROUPING_SHAPE_SIZE,
    GROUPING_SIDE_BY_SIDE_SIZE,
    GROUPING_ANIMATION_SPAWN_TIME,
    GROUPING_ANIMATION_REMOVE_TIME,
)
from src.compiler.relation_classifier import needs_arrow


TIMELINES_DIR = GROUPING_TIMELINES_DIR
SCENE_FILES_DIR = GROUPING_SCENE_FILES_DIR

CANVAS_X_MIN = GROUPING_CANVAS_X_MIN
CANVAS_X_MAX = GROUPING_CANVAS_X_MAX
CANVAS_Y_MIN = GROUPING_CANVAS_Y_MIN
CANVAS_Y_MAX = GROUPING_CANVAS_Y_MAX
GRID_MAX_COLS = GROUPING_GRID_MAX_COLS
LIST_X = GROUPING_LIST_X_POSITION
LIST_Y_START = GROUPING_LIST_Y_START
LIST_Y_STEP = GROUPING_LIST_Y_STEP
SHAPE_SIZE = GROUPING_SHAPE_SIZE
SIDE_SIZE = GROUPING_SIDE_BY_SIDE_SIZE
SPAWN_TIME = GROUPING_ANIMATION_SPAWN_TIME
REMOVE_TIME = GROUPING_ANIMATION_REMOVE_TIME

COLORS = ["blue", "green", "red", "orange", "purple", "yellow", "cyan", "pink"]


def _sanitize_ident(name: str) -> str:
    ident = re.sub(r"[^A-Za-z0-9_]", "_", name)
    ident = re.sub(r"_+", "_", ident).strip("_")
    if not ident or ident[0].isdigit():
        ident = "e_" + ident
    return ident.lower()[:30]


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


def _list_positions(count: int) -> list[tuple[float, float]]:
    """Vertical list layout — items stacked top to bottom, left-aligned."""
    positions = []
    for i in range(count):
        y = round(LIST_Y_START - i * LIST_Y_STEP, 1)
        positions.append((LIST_X, y))
    return positions


def _escape_dsl_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


# --- Timeline parsing ---

def _parse_timeline_file(filepath: Path) -> dict:
    content = filepath.read_text(encoding="utf-8", errors="replace")

    scene_match = re.search(r"SCENE\s+(\d+)", content)
    scene_number = int(scene_match.group(1)) if scene_match else 0

    type_match = re.search(r"TYPE:\s*(\S+)", content)
    scene_type = type_match.group(1).strip() if type_match else "NARRATION_ONLY"

    duration_match = re.search(r"TOTAL_DURATION:\s*([\d.]+)s", content)
    total_duration = float(duration_match.group(1)) if duration_match else 4.0

    voiceover_match = re.search(r'VOICEOVER:\s*"?(.+?)"?\s*$', content, re.MULTILINE)
    voiceover = voiceover_match.group(1).strip().strip('"') if voiceover_match else ""

    events = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or not re.match(r"[\d.]+s\s+", line):
            continue

        time_match = re.match(r'([\d.]+)s\s+(\w+)\s+"?(.*?)"?\s*\(([\d.]+)s\)\s*$', line)
        if not time_match:
            # HOLD has no target
            hold_match = re.match(r'([\d.]+)s\s+HOLD\s+\(([\d.]+)s\)', line)
            if hold_match:
                events.append({
                    "time": float(hold_match.group(1)),
                    "action": "HOLD",
                    "target": "",
                    "duration": float(hold_match.group(2)),
                })
            continue

        events.append({
            "time": float(time_match.group(1)),
            "action": time_match.group(2),
            "target": time_match.group(3),
            "duration": float(time_match.group(4)),
        })

    return {
        "scene_number": scene_number,
        "scene_type": scene_type,
        "total_duration": total_duration,
        "voiceover": voiceover,
        "events": events,
    }


# --- DSL generators per scene type ---

def _compile_title_card(parsed: dict) -> str:
    events = parsed["events"]
    text_event = next((e for e in events if e["action"] == "SHOW_TEXT"), None)
    if not text_event:
        return ""

    text = _escape_dsl_string(text_event["target"])
    duration = text_event["duration"]

    return "\n".join([
        'ELEMENT title TYPE shape',
        '    SHAPE rectangle',
        f'    TEXT "{text}"',
        '    POSITION (0.0,0.0)',
        '    SIZE 2.0',
        '    FILL blue',
        f'    SPAWN shape_popup {SPAWN_TIME}',
        f'    REMOVE shape_popout {REMOVE_TIME}',
        'END',
        '',
        'SEQUENCE',
        '    SPAWN title',
        f'    WAIT {round(duration, 1)}',
        '    CLOSE title',
        'END',
    ])


def _compile_quote(parsed: dict) -> str:
    events = parsed["events"]
    text_events = [e for e in events if e["action"] == "SHOW_TEXT"]

    elements = []
    sequence = []

    # quote text
    if len(text_events) >= 1:
        quote_text = _escape_dsl_string(text_events[0]["target"])
        elements.extend([
            'ELEMENT quote_text TYPE shape',
            '    SHAPE rectangle',
            f'    TEXT "{quote_text}"',
            '    POSITION (0.0,0.5)',
            '    SIZE 1.5',
            '    FILL blue',
            f'    SPAWN shape_popup {SPAWN_TIME}',
            f'    REMOVE shape_popout {REMOVE_TIME}',
            'END',
            '',
        ])
        sequence.append('    SPAWN quote_text')
        sequence.append(f'    WAIT {round(text_events[0]["duration"], 1)}')

    # attribution
    if len(text_events) >= 2:
        attr_text = _escape_dsl_string(text_events[1]["target"])
        elements.extend([
            'ELEMENT attribution TYPE shape',
            '    SHAPE rectangle',
            f'    TEXT "{attr_text}"',
            '    POSITION (0.0,-1.5)',
            '    SIZE 1.0',
            '    FILL green',
            f'    SPAWN shape_popup {SPAWN_TIME}',
            f'    REMOVE shape_popout {REMOVE_TIME}',
            'END',
            '',
        ])
        sequence.append('    SPAWN attribution')
        sequence.append(f'    WAIT {round(text_events[1]["duration"], 1)}')

    close_targets = []
    if len(text_events) >= 1:
        close_targets.append("quote_text")
    if len(text_events) >= 2:
        close_targets.append("attribution")

    if close_targets:
        sequence.append(f'    CLOSE {", ".join(close_targets)}')

    return "\n".join(elements + ['SEQUENCE'] + sequence + ['END'])


def _compile_side_by_side(parsed: dict) -> str:
    events = parsed["events"]
    spawn_events = [e for e in events if e["action"] == "SPAWN"]
    arrow_events = [e for e in events if e["action"] == "ARROW"]

    left_name = spawn_events[0]["target"] if len(spawn_events) > 0 else "Left"
    right_name = spawn_events[1]["target"] if len(spawn_events) > 1 else "Right"
    left_ident = _sanitize_ident(left_name)
    right_ident = _sanitize_ident(right_name)

    left_x = round(CANVAS_X_MIN / 2, 1)
    right_x = round(CANVAS_X_MAX / 2, 1)

    elements = [
        f'ELEMENT {left_ident} TYPE shape',
        '    SHAPE rectangle',
        f'    TEXT "{_escape_dsl_string(left_name)}"',
        f'    POSITION ({left_x},0.0)',
        f'    SIZE {SIDE_SIZE}',
        '    FILL blue',
        f'    SPAWN shape_popup {SPAWN_TIME}',
        f'    REMOVE shape_popout {REMOVE_TIME}',
        'END',
        '',
        f'ELEMENT {right_ident} TYPE shape',
        '    SHAPE rectangle',
        f'    TEXT "{_escape_dsl_string(right_name)}"',
        f'    POSITION ({right_x},0.0)',
        f'    SIZE {SIDE_SIZE}',
        '    FILL red',
        f'    SPAWN shape_popup {SPAWN_TIME}',
        f'    REMOVE shape_popout {REMOVE_TIME}',
        'END',
        '',
    ]

    sequence = [
        f'    SPAWN {left_ident}',
    ]

    # wait between left and right spawn
    if len(spawn_events) > 1:
        gap = round(spawn_events[1]["time"] - spawn_events[0]["time"], 1)
        if gap > 0:
            sequence.append(f'    WAIT {gap}')

    sequence.append(f'    SPAWN {right_ident}')

    if arrow_events:
        arrow_target = arrow_events[0]["target"]
        # parse label from "Left --[label]--> Right"
        label_match = re.search(r'--\[(.+?)\]-->', arrow_target)
        label = label_match.group(1) if label_match else "relates to"

        # only draw arrow if the verb warrants it
        if not needs_arrow(label):
            arrow_events = []

    if arrow_events:
        label_match = re.search(r'--\[(.+?)\]-->', arrow_events[0]["target"])
        label = label_match.group(1) if label_match else "relates to"
        arrow_ident = "arrow_1"
        arrow_duration = arrow_events[0]["duration"]
        gap = round(arrow_events[0]["time"] - (spawn_events[1]["time"] if len(spawn_events) > 1 else 0), 1)
        if gap > 0:
            sequence.append(f'    WAIT {gap}')

        # arrow from right edge of left shape to left edge of right shape
        arrow_start_x = round(left_x + SIDE_SIZE / 2, 1)
        arrow_end_x = round(right_x - SIDE_SIZE / 2, 1)

        elements.extend([
            f'ELEMENT {arrow_ident} TYPE arrow',
            f'    TEXT "{_escape_dsl_string(label)}"',
            f'    POSITION ({arrow_start_x},0.0)',
            f'    SPAWN unidirectional_dotted_spawn {arrow_duration}',
            f'    MOVE straight TO ({arrow_end_x},0.0) DURATION {arrow_duration}',
            f'    REMOVE unidirectional_dotted_remove {REMOVE_TIME}',
            'END',
            '',
        ])
        sequence.append(f'    SPAWN {arrow_ident}')
        sequence.append(f'    MOVE {arrow_ident}')

    # hold remaining time then close
    fade_event = next((e for e in events if e["action"] == "FADE"), None)
    if fade_event:
        last_event_end = max(e["time"] + e.get("duration", 0) for e in events if e["action"] != "FADE")
        remaining = round(fade_event["time"] - last_event_end, 1)
        if remaining > 0:
            sequence.append(f'    WAIT {remaining}')

    close_targets = [left_ident, right_ident]
    if arrow_events:
        close_targets.append("arrow_1")
    sequence.append(f'    CLOSE {", ".join(close_targets)}')

    return "\n".join(elements + ['SEQUENCE'] + sequence + ['END'])


def _compile_bullet_list(parsed: dict) -> str:
    events = parsed["events"]
    list_event = next((e for e in events if e["action"] == "SHOW_LIST"), None)
    highlight_events = [e for e in events if e["action"] == "HIGHLIGHT"]

    if not list_event:
        return _compile_narration_only(parsed)

    items = list_event["target"].split("|")
    positions = _list_positions(len(items))

    elements = []
    idents = []
    for i, item in enumerate(items):
        ident = _sanitize_ident(item)[:25] + f"_{i}"
        idents.append(ident)
        x, y = positions[i]
        color = COLORS[i % len(COLORS)]
        elements.extend([
            f'ELEMENT {ident} TYPE shape',
            '    SHAPE rectangle',
            f'    TEXT "{_escape_dsl_string(item)}"',
            f'    POSITION ({x},{y})',
            '    SIZE 0.8',
            f'    FILL {color}',
            f'    SPAWN shape_popup {SPAWN_TIME}',
            f'    REMOVE shape_popout {REMOVE_TIME}',
            'END',
            '',
        ])

    sequence = [f'    SPAWN {", ".join(idents)}']

    # add waits between highlights
    for i, h_event in enumerate(highlight_events):
        sequence.append(f'    WAIT {round(h_event["duration"], 1)}')

    # remaining hold if any
    fade_event = next((e for e in events if e["action"] == "FADE"), None)
    if fade_event:
        sequence.append(f'    CLOSE {", ".join(idents)}')

    return "\n".join(elements + ['SEQUENCE'] + sequence + ['END'])


def _compile_flow_diagram(parsed: dict) -> str:
    events = parsed["events"]
    spawn_events = [e for e in events if e["action"] == "SPAWN"]
    arrow_events = [e for e in events if e["action"] == "ARROW"]

    entity_names = [e["target"] for e in spawn_events]
    positions = _grid_positions(len(entity_names))
    entity_idents = {}
    entity_positions = {}

    elements = []
    for i, name in enumerate(entity_names):
        ident = _sanitize_ident(name)
        entity_idents[name] = ident
        x, y = positions[i]
        entity_positions[name] = (x, y)
        color = COLORS[i % len(COLORS)]
        elements.extend([
            f'ELEMENT {ident} TYPE shape',
            '    SHAPE rectangle',
            f'    TEXT "{_escape_dsl_string(name)}"',
            f'    POSITION ({x},{y})',
            '    SIZE 1.0',
            f'    FILL {color}',
            f'    SPAWN shape_popup {SPAWN_TIME}',
            f'    REMOVE shape_popout {REMOVE_TIME}',
            'END',
            '',
        ])

    sequence = []
    all_idents = list(entity_idents.values())

    # spawn entities with gaps
    prev_time = 0.0
    for e in spawn_events:
        gap = round(e["time"] - prev_time, 1)
        if gap > 0 and sequence:
            sequence.append(f'    WAIT {gap}')
        sequence.append(f'    SPAWN {entity_idents[e["target"]]}')
        prev_time = e["time"] + e["duration"]

    # arrows — only include if the verb warrants it
    arrow_idents = []
    for i, a_event in enumerate(arrow_events):
        target = a_event["target"]

        # parse verb if present: "src --[verb]--> dst" or "src --> dst"
        verb_match = re.search(r'--\[(.+?)\]-->', target)
        verb = verb_match.group(1) if verb_match else ""

        if verb and not needs_arrow(verb):
            continue

        arrow_ident = f"arrow_{i + 1}"
        arrow_idents.append(arrow_ident)

        # parse "src --> dst" (strip verb brackets if present)
        clean_target = re.sub(r'\s*--\[.+?\]-->\s*', ' --> ', target)
        parts = re.split(r'\s*-->\s*', clean_target)
        src_name = parts[0].strip() if len(parts) > 0 else ""
        dst_name = parts[1].strip() if len(parts) > 1 else ""

        from_pos = entity_positions.get(src_name, (0.0, 0.0))
        to_pos = entity_positions.get(dst_name, (0.0, 0.0))

        # offset arrow start/end to shape edges (shape size = 1.0)
        shape_half = 0.5
        dx = to_pos[0] - from_pos[0]
        dy = to_pos[1] - from_pos[1]
        dist = max((dx**2 + dy**2) ** 0.5, 0.01)
        nx, ny = dx / dist, dy / dist
        arrow_start = (round(from_pos[0] + nx * shape_half, 1), round(from_pos[1] + ny * shape_half, 1))
        arrow_end = (round(to_pos[0] - nx * shape_half, 1), round(to_pos[1] - ny * shape_half, 1))

        elements.extend([
            f'ELEMENT {arrow_ident} TYPE arrow',
            f'    TEXT ""',
            f'    POSITION ({arrow_start[0]},{arrow_start[1]})',
            f'    SPAWN unidirectional_dotted_spawn {a_event["duration"]}',
            f'    MOVE straight TO ({arrow_end[0]},{arrow_end[1]}) DURATION {a_event["duration"]}',
            f'    REMOVE unidirectional_dotted_remove {REMOVE_TIME}',
            'END',
            '',
        ])

        gap = round(a_event["time"] - prev_time, 1)
        if gap > 0:
            sequence.append(f'    WAIT {gap}')
        sequence.append(f'    SPAWN {arrow_ident}')
        sequence.append(f'    MOVE {arrow_ident}')
        prev_time = a_event["time"] + a_event["duration"]

    fade_event = next((e for e in events if e["action"] == "FADE"), None)
    if fade_event:
        remaining = round(fade_event["time"] - prev_time, 1)
        if remaining > 0:
            sequence.append(f'    WAIT {remaining}')
        sequence.append(f'    CLOSE {", ".join(all_idents + arrow_idents)}')

    return "\n".join(elements + ['SEQUENCE'] + sequence + ['END'])


def _compile_show_resource(parsed: dict) -> str:
    events = parsed["events"]
    resource_event = next((e for e in events if e["action"] == "SHOW_RESOURCE"), None)
    text_event = next((e for e in events if e["action"] == "SHOW_TEXT"), None)

    if not resource_event:
        return _compile_narration_only(parsed)

    resource_path = resource_event["target"]
    duration = parsed["total_duration"]

    elements = [
        'ELEMENT resource TYPE image',
        f'    URL "{_escape_dsl_string(resource_path)}"',
        '    POSITION (0.0,0.5)',
        '    SIZE 4.0',
        f'    SPAWN image_popup {SPAWN_TIME}',
        f'    REMOVE image_popout {REMOVE_TIME}',
        'END',
        '',
    ]

    sequence = ['    SPAWN resource']
    close_targets = ['resource']

    if text_event:
        caption_text = _escape_dsl_string(text_event["target"])
        elements.extend([
            'ELEMENT caption TYPE shape',
            '    SHAPE rectangle',
            f'    TEXT "{caption_text}"',
            '    POSITION (0.0,-2.5)',
            '    SIZE 0.8',
            '    FILL green',
            f'    SPAWN shape_popup {SPAWN_TIME}',
            f'    REMOVE shape_popout {REMOVE_TIME}',
            'END',
            '',
        ])
        sequence.append('    SPAWN caption')
        close_targets.append('caption')

    sequence.append(f'    WAIT {round(duration - 1.0, 1)}')
    sequence.append(f'    CLOSE {", ".join(close_targets)}')

    return "\n".join(elements + ['SEQUENCE'] + sequence + ['END'])


def _compile_narration_only(parsed: dict) -> str:
    events = parsed["events"]
    spawn_events = [e for e in events if e["action"] == "SPAWN"]

    if not spawn_events:
        # nothing to display — just a wait
        return "\n".join([
            'ELEMENT placeholder TYPE shape',
            '    SHAPE rectangle',
            '    TEXT ""',
            '    POSITION (0.0,0.0)',
            '    SIZE 0.1',
            '    FILL blue',
            '    SPAWN shape_popup 0.1',
            '    REMOVE shape_popout 0.1',
            'END',
            '',
            'SEQUENCE',
            '    SPAWN placeholder',
            f'    WAIT {round(parsed["total_duration"], 1)}',
            '    CLOSE placeholder',
            'END',
        ])

    entity_names = [e["target"] for e in spawn_events]
    positions = _grid_positions(len(entity_names))
    entity_idents = {}

    elements = []
    for i, name in enumerate(entity_names):
        ident = _sanitize_ident(name) + f"_{i}"
        entity_idents[name] = ident
        x, y = positions[i]
        color = COLORS[i % len(COLORS)]
        elements.extend([
            f'ELEMENT {ident} TYPE shape',
            '    SHAPE rectangle',
            f'    TEXT "{_escape_dsl_string(name)}"',
            f'    POSITION ({x},{y})',
            '    SIZE 1.0',
            f'    FILL {color}',
            f'    SPAWN shape_popup {SPAWN_TIME}',
            f'    REMOVE shape_popout {REMOVE_TIME}',
            'END',
            '',
        ])

    sequence = []
    all_idents = list(entity_idents.values())
    prev_time = 0.0

    for e in spawn_events:
        gap = round(e["time"] - prev_time, 1)
        if gap > 0 and sequence:
            sequence.append(f'    WAIT {gap}')
        sequence.append(f'    SPAWN {entity_idents[e["target"]]}')
        prev_time = e["time"] + e["duration"]

    # handle fade events
    fade_events = [e for e in events if e["action"] == "FADE"]
    non_star_fades = [e for e in fade_events if e["target"] != "*"]
    star_fade = next((e for e in fade_events if e["target"] == "*"), None)

    for f_event in non_star_fades:
        gap = round(f_event["time"] - prev_time, 1)
        if gap > 0:
            sequence.append(f'    WAIT {gap}')
        target_ident = entity_idents.get(f_event["target"])
        if target_ident:
            sequence.append(f'    CLOSE {target_ident}')
            all_idents = [i for i in all_idents if i != target_ident]
        prev_time = f_event["time"] + f_event["duration"]

    if star_fade:
        remaining = round(star_fade["time"] - prev_time, 1)
        if remaining > 0:
            sequence.append(f'    WAIT {remaining}')
        if all_idents:
            sequence.append(f'    CLOSE {", ".join(all_idents)}')
    else:
        # no final fade — hold until end
        remaining = round(parsed["total_duration"] - prev_time, 1)
        if remaining > 0:
            sequence.append(f'    WAIT {remaining}')
        if all_idents:
            sequence.append(f'    CLOSE {", ".join(all_idents)}')

    return "\n".join(elements + ['SEQUENCE'] + sequence + ['END'])


COMPILERS = {
    "TITLE_CARD": _compile_title_card,
    "QUOTE": _compile_quote,
    "SIDE_BY_SIDE": _compile_side_by_side,
    "BULLET_LIST": _compile_bullet_list,
    "FLOW_DIAGRAM": _compile_flow_diagram,
    "SHOW_RESOURCE": _compile_show_resource,
    "NARRATION_ONLY": _compile_narration_only,
}


# --- File processing ---

def compile_timeline(filepath: Path) -> str:
    parsed = _parse_timeline_file(filepath)
    compiler = COMPILERS.get(parsed["scene_type"], _compile_narration_only)
    return compiler(parsed)


def process_timeline_file(filepath: Path) -> None:
    SCENE_FILES_DIR.mkdir(parents=True, exist_ok=True)
    output_file = SCENE_FILES_DIR / f"{filepath.stem}.scene"
    if output_file.exists():
        return
    dsl = compile_timeline(filepath)
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
        dsl = compile_timeline(f)
        output_file.write_text(dsl, encoding="utf-8")
        compiled += 1
    print(f"Compiled {compiled} scene file(s).")


# --- Watchdog ---

def start_watcher():
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    from concurrent.futures import ThreadPoolExecutor

    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)
    SCENE_FILES_DIR.mkdir(parents=True, exist_ok=True)

    class TimelineHandler(FileSystemEventHandler):
        def __init__(self):
            self._executor = ThreadPoolExecutor()

        def on_created(self, event):
            if event.is_directory:
                return
            filepath = Path(event.src_path)
            if filepath.suffix == ".txt" and filepath.stem.startswith("timeline_"):
                self._executor.submit(process_timeline_file, filepath)

    handler = TimelineHandler()
    observer = Observer()
    observer.schedule(handler, str(TIMELINES_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer) -> None:
    observer.stop()
    observer.join()
