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
    GROUPING_RESOURCE_SIZE,
    GROUPING_HEADING_TARGET_WIDTH,
    GROUPING_QUOTE_TARGET_WIDTH,
    GROUPING_LIST_ITEM_X,
    GROUPING_LIST_ITEM_Y_TOP,
    GROUPING_LIST_ITEM_SPACING,
    GROUPING_LIST_ITEM_TARGET_WIDTH,
)
from src.icons.icon_downloader import icon_path, download_icon


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
RESOURCE_SIZE = GROUPING_RESOURCE_SIZE
HEADING_TARGET_WIDTH = GROUPING_HEADING_TARGET_WIDTH
QUOTE_TARGET_WIDTH = GROUPING_QUOTE_TARGET_WIDTH
LIST_ITEM_X = GROUPING_LIST_ITEM_X
LIST_ITEM_Y_TOP = GROUPING_LIST_ITEM_Y_TOP
LIST_ITEM_SPACING = GROUPING_LIST_ITEM_SPACING
LIST_ITEM_TARGET_WIDTH = GROUPING_LIST_ITEM_TARGET_WIDTH

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
    """Legacy fixed-grid layout — kept for non-entity SPAWN flows."""
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


# Per-character horizontal width for bold Arial scaled to text_height ~0.7.
# Empirical fit to Manim's Text rendering — slightly conservative.
_CHAR_WIDTH_AT_TEXT_HEIGHT_07 = 0.45


def _estimate_entity_width(name: str, kind: str, has_icon: bool) -> float:
    """Estimate the rendered width (manim units) of an entity at SPAWN time."""
    if has_icon:
        # Image element with SIZE 2.0 — width is at most 2.0 manim units
        return 2.0
    chars = len(name)
    if kind == "action":
        chars += 2  # leading "▸ " marker
    text_width = chars * _CHAR_WIDTH_AT_TEXT_HEIGHT_07
    # Match shape min_width and padding from shape_objects.py (min_width=2.5, padding_x=0.3)
    return max(2.5, text_width + 0.6)


def _pack_positions(widths: list[float], canvas_width: float,
                    canvas_y_min: float, canvas_y_max: float,
                    gap: float = 0.5) -> list[tuple[float, float]]:
    """Pack entities into rows that fit within canvas_width, centered.

    Greedy line-breaking: append items to the current row until the next item
    would exceed canvas_width, then wrap. Each row is centered horizontally;
    rows are evenly distributed vertically.
    """
    if not widths:
        return []

    # Build rows
    rows: list[list[float]] = []
    current_row: list[float] = []
    current_total = 0.0
    for w in widths:
        prospective = current_total + (gap if current_row else 0) + w
        if current_row and prospective > canvas_width:
            rows.append(current_row)
            current_row = [w]
            current_total = w
        else:
            current_row.append(w)
            current_total = prospective
    if current_row:
        rows.append(current_row)

    n_rows = len(rows)
    canvas_height = canvas_y_max - canvas_y_min
    y_step = canvas_height / (n_rows + 1)

    positions: list[tuple[float, float]] = []
    for row_idx, row_widths in enumerate(rows):
        row_total = sum(row_widths) + gap * (len(row_widths) - 1)
        x_cursor = -row_total / 2
        y = round(canvas_y_max - (row_idx + 1) * y_step, 2)
        for w in row_widths:
            cx = round(x_cursor + w / 2, 2)
            positions.append((cx, y))
            x_cursor += w + gap

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

    # First pass: resolve each SPAWN event's metadata, then width-aware-pack
    # them into rows that fit within the canvas.
    spawn_events = [e for e in events if e["action"] == "SPAWN"]
    spawn_meta: list[dict] = []
    for ev in spawn_events:
        raw = ev["target"]
        parts = raw.split("|||")
        name = parts[0].strip()
        kind = parts[1].strip().lower() if len(parts) > 1 else "concrete"
        has_icon = False
        if kind == "concrete":
            svg = icon_path(name)
            if not svg.exists():
                download_icon(name)
            has_icon = svg.exists()
        spawn_meta.append({
            "name": name,
            "kind": kind,
            "has_icon": has_icon,
            "width": _estimate_entity_width(name, kind, has_icon),
        })

    canvas_width = CANVAS_X_MAX - CANVAS_X_MIN
    positions = _pack_positions(
        [m["width"] for m in spawn_meta],
        canvas_width=canvas_width,
        canvas_y_min=CANVAS_Y_MIN,
        canvas_y_max=CANVAS_Y_MAX,
        gap=0.5,
    )

    # Walk events in time order
    sorted_events = sorted(events, key=lambda e: e["time"])
    prev_time = 0.0

    for event in sorted_events:
        gap = round(event["time"] - prev_time, 1)
        if gap > 0:
            sequence.append(f'    WAIT {gap}')

        if event["action"] == "SPAWN":
            spawn_idx = len(ident_by_index)
            meta = spawn_meta[spawn_idx]
            target = meta["name"]
            kind = meta["kind"]
            has_icon = meta["has_icon"]

            occ = target_occurrences.get(target, 0)
            target_occurrences[target] = occ + 1

            ident = _sanitize_ident(target, suffix=spawn_idx)
            ident_by_target[(target, occ)] = ident
            ident_by_index.append(ident)

            x, y = positions[spawn_idx] if spawn_idx < len(positions) else (0.0, 0.0)
            color = COLORS[color_idx % len(COLORS)]
            color_idx += 1

            if kind == "abstract":
                # Bold italic text only — no box
                elements.extend([
                    f'ELEMENT {ident} TYPE shape',
                    f'    SHAPE entity_abstract',
                    f'    TEXT "{_escape_dsl_string(target)}"',
                    f'    POSITION ({x},{y})',
                    f'    SIZE {SHAPE_SIZE}',
                    f'    SPAWN shape_popup {SPAWN_TIME}',
                    f'    REMOVE shape_popout {REMOVE_TIME}',
                    f'END',
                    f'',
                ])
            elif kind == "action":
                # Bold text with leading marker — no box
                elements.extend([
                    f'ELEMENT {ident} TYPE shape',
                    f'    SHAPE entity_action',
                    f'    TEXT "{_escape_dsl_string(target)}"',
                    f'    POSITION ({x},{y})',
                    f'    SIZE {SHAPE_SIZE}',
                    f'    SPAWN shape_popup {SPAWN_TIME}',
                    f'    REMOVE shape_popout {REMOVE_TIME}',
                    f'END',
                    f'',
                ])
            else:
                # Concrete: icon download already attempted in the metadata pass.
                # has_icon reflects whether the SVG is on disk now.
                if has_icon:
                    svg_path = icon_path(target)
                    # Entity icons are sized via the image element's SIZE param
                    # (ImageObject treats values < 3.0 as icon-mode and uses size
                    # as the max dimension). 2.0 manim units gives a ~270px icon
                    # at 1080p — alongside other entities, not full-screen.
                    elements.extend([
                        f'ELEMENT {ident} TYPE image',
                        f'    URL "{_escape_dsl_string(str(svg_path))}"',
                        f'    TEXT "{_escape_dsl_string(target)}"',
                        f'    POSITION ({x},{y})',
                        f'    SIZE 2.0',
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
                f'    SIZE {RESOURCE_SIZE}',
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
                f'    SIZE {HEADING_TARGET_WIDTH}',
                f'    SPAWN shape_popup {SPAWN_TIME}',
                f'    REMOVE shape_popout {REMOVE_TIME}',
                f'END',
                f'',
            ])
            sequence.append(f'    SPAWN {ident}')
            ident_by_index.append(ident)
            prev_time = event["time"]

        elif event["action"] == "SHOW_LIST_ITEM":
            li_idx = len([i for i in ident_by_index if i.startswith("listitem_")])
            ident = f"listitem_{li_idx}"
            # Stack list items vertically, left-aligned (each row spaced LIST_ITEM_SPACING units)
            y = LIST_ITEM_Y_TOP - li_idx * LIST_ITEM_SPACING
            x = LIST_ITEM_X
            elements.extend([
                f'ELEMENT {ident} TYPE shape',
                f'    SHAPE list_item',
                f'    TEXT "{_escape_dsl_string(event["target"])}"',
                f'    POSITION ({x},{y})',
                f'    SIZE {LIST_ITEM_TARGET_WIDTH}',
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
                f'    SIZE {QUOTE_TARGET_WIDTH}',
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
            else:
                # Close a single named entity (used for sliding-window eviction).
                # Resolve the most-recent occurrence of `target` and emit CLOSE.
                target_name = event["target"]
                ident_to_close = None
                # Find the latest (target, occ) entry whose ident is still active
                for (t, occ) in sorted(ident_by_target.keys(), key=lambda k: -k[1]):
                    if t == target_name and ident_by_target[(t, occ)] in ident_by_index:
                        ident_to_close = ident_by_target[(t, occ)]
                        del ident_by_target[(t, occ)]
                        break
                if ident_to_close is not None:
                    sequence.append(f'    CLOSE {ident_to_close}')
                    if ident_to_close in ident_by_index:
                        ident_by_index.remove(ident_to_close)
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
