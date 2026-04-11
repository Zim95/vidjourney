"""
Timeline generator from storyboard files.

Each scene type has a fixed visual pattern:
  TITLE_CARD    — big text appears, holds, fades
  QUOTE         — quote text + attribution after a pause
  SIDE_BY_SIDE  — left box, right box, optional arrow
  BULLET_LIST   — all bullets visible, current one highlights as narrated
  NARRATION_ONLY — keywords spawn at the moment they're narrated, sliding window
  SHOW_RESOURCE — image appears, optional caption below
  FLOW_DIAGRAM  — entities spawn, arrows draw between them

Uses YAKE for keyword extraction on NARRATION_ONLY scenes.
"""
import re
from collections import deque
from pathlib import Path
from dataclasses import dataclass, field

from src.utils import logger

import yake

from src.config.constants import (
    GROUPING_STORYBOARD_DIR,
    GROUPING_TIMELINES_DIR,
    GROUPING_WORDS_PER_MINUTE,
    GROUPING_MIN_SCENE_DURATION,
    GROUPING_SPAWN_DURATION,
    GROUPING_FADE_DURATION,
    GROUPING_ARROW_DURATION,
    GROUPING_MAX_ON_SCREEN,
)


STORYBOARD_DIR = GROUPING_STORYBOARD_DIR
TIMELINES_DIR = GROUPING_TIMELINES_DIR
WORDS_PER_MINUTE = GROUPING_WORDS_PER_MINUTE
MIN_SCENE_DURATION = GROUPING_MIN_SCENE_DURATION
SPAWN_DURATION = GROUPING_SPAWN_DURATION
FADE_DURATION = GROUPING_FADE_DURATION
ARROW_DURATION = GROUPING_ARROW_DURATION
MAX_ON_SCREEN = GROUPING_MAX_ON_SCREEN

_kw_extractor = yake.KeywordExtractor(
    lan="en",
    n=2,
    top=6,
    dedupLim=0.3,
)


# --- Data classes ---

@dataclass
class TimelineEvent:
    time: float
    action: str
    target: str
    duration: float


@dataclass
class SceneTimeline:
    scene_number: int
    scene_type: str
    voiceover: str
    total_duration: float
    events: list[TimelineEvent] = field(default_factory=list)


# --- Utilities ---

def _reading_time(text: str) -> float:
    words = len(text.split())
    seconds = (words / WORDS_PER_MINUTE) * 60.0
    return max(MIN_SCENE_DURATION, seconds)


def _word_offset_time(text: str, word_position: int, total_duration: float) -> float:
    total_words = len(text.split())
    if total_words == 0:
        return 0.0
    return (word_position / total_words) * total_duration


def _find_word_position(text: str, keyword: str) -> int:
    words = text.lower().split()
    kw_words = keyword.lower().split()
    for i in range(len(words) - len(kw_words) + 1):
        if words[i:i + len(kw_words)] == kw_words:
            return i
    for i, w in enumerate(words):
        if kw_words[0] in w:
            return i
    return len(words) // 2


def _extract_keywords(text: str) -> list[str]:
    keywords = _kw_extractor.extract_keywords(text)
    kw_texts = [kw for kw, _score in keywords]
    filtered = []
    for kw in kw_texts:
        is_substring = any(
            kw.lower() != other.lower() and kw.lower() in other.lower()
            for other in kw_texts
        )
        if not is_substring:
            filtered.append(kw)
    positioned = []
    for kw in filtered:
        pos = _find_word_position(text, kw)
        positioned.append((pos, kw))
    positioned.sort(key=lambda x: x[0])
    return [kw for _, kw in positioned]


def _extract_quoted(text: str) -> list[str]:
    normalized = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    return re.findall(r'"([^"]+)"', normalized)


# --- Storyboard parsing ---

def _parse_storyboard_file(filepath: Path) -> list[dict]:
    content = filepath.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"---+", content)

    scenes = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        scene_match = re.search(r"SCENE\s+(\d+)", block)
        if not scene_match:
            continue

        scene = {"scene_number": int(scene_match.group(1))}

        type_match = re.search(r"TYPE:\s*(\S+)", block)
        scene["type"] = type_match.group(1).strip() if type_match else "NARRATION_ONLY"

        voiceover_match = re.search(r'VOICEOVER:\s*"?(.+?)"?\s*$', block, re.MULTILINE)
        scene["voiceover"] = voiceover_match.group(1).strip().strip('"') if voiceover_match else ""

        screen_match = re.search(r"SCREEN:\s*(.+?)(?=\n[A-Z]+:|\Z)", block, re.DOTALL)
        scene["screen"] = screen_match.group(1).strip() if screen_match else ""

        resource_match = re.search(r"RESOURCE:\s*(\S+)", block)
        scene["resource"] = resource_match.group(1).strip() if resource_match else None

        caption_match = re.search(r'CAPTION:\s*"?(.+?)"?\s*$', block, re.MULTILINE)
        scene["caption"] = caption_match.group(1).strip().strip('"') if caption_match else None

        scenes.append(scene)

    return scenes


# --- Timeline builders per scene type ---

def _build_title_card(scene: dict) -> SceneTimeline:
    voiceover = scene["voiceover"]
    duration = _reading_time(voiceover)
    return SceneTimeline(
        scene_number=scene["scene_number"],
        scene_type="TITLE_CARD",
        voiceover=voiceover,
        total_duration=round(duration, 2),
        events=[
            TimelineEvent(time=0.0, action="SHOW_TEXT", target=voiceover, duration=round(duration - FADE_DURATION, 2)),
            TimelineEvent(time=round(duration - FADE_DURATION, 2), action="FADE", target="*", duration=FADE_DURATION),
        ],
    )


def _build_quote(scene: dict) -> SceneTimeline:
    voiceover = scene["voiceover"]
    duration = _reading_time(voiceover)

    # split quote from attribution at em-dash
    attribution = ""
    quote_text = voiceover
    attr_match = re.search(r'\s*[—–]\s*(.+)$', voiceover)
    if attr_match:
        attribution = attr_match.group(1).strip()
        quote_text = voiceover[:attr_match.start()].strip()

    attr_time = round(duration * 0.7, 2)
    events = [
        TimelineEvent(time=0.0, action="SHOW_TEXT", target=quote_text, duration=attr_time),
    ]
    if attribution:
        events.append(TimelineEvent(time=attr_time, action="SHOW_TEXT", target=f"— {attribution}", duration=round(duration - attr_time - FADE_DURATION, 2)))
    events.append(TimelineEvent(time=round(duration - FADE_DURATION, 2), action="FADE", target="*", duration=FADE_DURATION))

    return SceneTimeline(
        scene_number=scene["scene_number"],
        scene_type="QUOTE",
        voiceover=voiceover,
        total_duration=round(duration, 2),
        events=events,
    )


def _build_side_by_side(scene: dict) -> SceneTimeline:
    voiceover = scene["voiceover"]
    duration = _reading_time(voiceover)
    screen = scene["screen"]

    labels = _extract_quoted(screen)
    left = labels[0] if len(labels) > 0 else "Left"
    right = labels[1] if len(labels) > 1 else "Right"

    events = [
        TimelineEvent(time=0.0, action="SPAWN", target=left, duration=SPAWN_DURATION),
        TimelineEvent(time=round(duration * 0.3, 2), action="SPAWN", target=right, duration=SPAWN_DURATION),
    ]

    # check for arrow
    normalized_screen = screen.replace("\u201c", '"').replace("\u201d", '"')
    arrow_match = re.search(r'arrow.*?(?:labeled\s+)?["\']([^"\']+)', normalized_screen, re.IGNORECASE)
    if arrow_match:
        events.append(TimelineEvent(
            time=round(duration * 0.5, 2),
            action="ARROW",
            target=f"{left} --[{arrow_match.group(1)}]--> {right}",
            duration=ARROW_DURATION,
        ))

    events.append(TimelineEvent(time=round(duration - FADE_DURATION, 2), action="FADE", target="*", duration=FADE_DURATION))

    return SceneTimeline(
        scene_number=scene["scene_number"],
        scene_type="SIDE_BY_SIDE",
        voiceover=voiceover,
        total_duration=round(duration, 2),
        events=events,
    )


def _build_bullet_list(scene: dict) -> SceneTimeline:
    voiceover = scene["voiceover"]
    screen = scene["screen"]
    duration = _reading_time(voiceover)

    # extract items from screen or voiceover
    items = _extract_quoted(screen)
    if not items:
        # try splitting by bullet markers in voiceover
        bullet_parts = re.split(r'[•·]\s*', voiceover)
        items = [p.strip() for p in bullet_parts if p.strip() and len(p.strip()) > 10]
    if not items:
        items = _extract_keywords(voiceover)

    if not items:
        return _build_narration_only(scene)

    # all items visible from start, highlight one at a time
    events = [
        TimelineEvent(time=0.0, action="SHOW_LIST", target="|".join(items), duration=SPAWN_DURATION),
    ]

    time_per_item = (duration - SPAWN_DURATION - FADE_DURATION) / max(len(items), 1)
    for i, item in enumerate(items):
        t = round(SPAWN_DURATION + i * time_per_item, 2)
        events.append(TimelineEvent(time=t, action="HIGHLIGHT", target=item, duration=round(time_per_item, 2)))

    events.append(TimelineEvent(time=round(duration - FADE_DURATION, 2), action="FADE", target="*", duration=FADE_DURATION))

    return SceneTimeline(
        scene_number=scene["scene_number"],
        scene_type="BULLET_LIST",
        voiceover=voiceover,
        total_duration=round(duration, 2),
        events=events,
    )


def _build_flow_diagram(scene: dict) -> SceneTimeline:
    voiceover = scene["voiceover"]
    screen = scene["screen"]
    duration = _reading_time(voiceover)

    entities = _extract_quoted(screen)
    if not entities:
        entities = _extract_keywords(voiceover)

    events = []
    t = 0.0
    for entity in entities:
        events.append(TimelineEvent(time=round(t, 2), action="SPAWN", target=entity, duration=SPAWN_DURATION))
        t += SPAWN_DURATION

    # look for arrows in screen
    arrow_matches = re.findall(r'"([^"]+)"\s*(?:→|->|-->|to)\s*"([^"]+)"', screen.replace("\u201c", '"').replace("\u201d", '"'), re.IGNORECASE)
    for src, dst in arrow_matches:
        events.append(TimelineEvent(time=round(t, 2), action="ARROW", target=f"{src} --> {dst}", duration=ARROW_DURATION))
        t += ARROW_DURATION

    events.append(TimelineEvent(time=round(duration - FADE_DURATION, 2), action="FADE", target="*", duration=FADE_DURATION))

    return SceneTimeline(
        scene_number=scene["scene_number"],
        scene_type="FLOW_DIAGRAM",
        voiceover=voiceover,
        total_duration=round(duration, 2),
        events=events,
    )


def _build_show_resource(scene: dict) -> SceneTimeline:
    voiceover = scene["voiceover"]
    resource = scene.get("resource") or ""
    caption = scene.get("caption")
    duration = _reading_time(voiceover)

    events = [
        TimelineEvent(time=0.0, action="SHOW_RESOURCE", target=resource, duration=SPAWN_DURATION),
    ]
    if caption:
        events.append(TimelineEvent(time=round(SPAWN_DURATION, 2), action="SHOW_TEXT", target=caption, duration=round(duration - SPAWN_DURATION - FADE_DURATION, 2)))
    events.append(TimelineEvent(time=round(duration - FADE_DURATION, 2), action="FADE", target="*", duration=FADE_DURATION))

    return SceneTimeline(
        scene_number=scene["scene_number"],
        scene_type="SHOW_RESOURCE",
        voiceover=voiceover,
        total_duration=round(duration, 2),
        events=events,
    )


def _build_narration_only(scene: dict) -> SceneTimeline:
    """
    Keywords spawn at the moment the narrator says them.
    Sliding window: oldest fades when at max capacity.
    No final fade — visuals carry over to next scene.
    """
    voiceover = scene["voiceover"]
    duration = _reading_time(voiceover)

    keywords = _extract_keywords(voiceover)
    if not keywords:
        return SceneTimeline(
            scene_number=scene["scene_number"],
            scene_type="NARRATION_ONLY",
            voiceover=voiceover,
            total_duration=round(duration, 2),
            events=[TimelineEvent(time=0.0, action="HOLD", target="", duration=round(duration, 2))],
        )

    events = []
    on_screen: deque[str] = deque()

    for kw in keywords:
        word_pos = _find_word_position(voiceover, kw)
        t = round(_word_offset_time(voiceover, word_pos, duration), 2)

        if len(on_screen) >= MAX_ON_SCREEN:
            oldest = on_screen.popleft()
            events.append(TimelineEvent(time=t, action="FADE", target=oldest, duration=FADE_DURATION))

        events.append(TimelineEvent(time=t, action="SPAWN", target=kw, duration=SPAWN_DURATION))
        on_screen.append(kw)

    # no final fade — visuals carry over to next scene

    return SceneTimeline(
        scene_number=scene["scene_number"],
        scene_type="NARRATION_ONLY",
        voiceover=voiceover,
        total_duration=round(duration, 2),
        events=events,
    )


SCENE_BUILDERS = {
    "TITLE_CARD": _build_title_card,
    "QUOTE": _build_quote,
    "SIDE_BY_SIDE": _build_side_by_side,
    "BULLET_LIST": _build_bullet_list,
    "FLOW_DIAGRAM": _build_flow_diagram,
    "SHOW_RESOURCE": _build_show_resource,
    "NARRATION_ONLY": _build_narration_only,
}


# --- Cross-scene carry-over ---

def _apply_carry_over(timelines: list[SceneTimeline]) -> list[SceneTimeline]:
    """
    Ensure screen is never blank between scenes.
    If next scene starts with a visual within 1s, current scene can fade.
    Otherwise, remove current scene's final fade so visuals persist.
    """
    for i in range(len(timelines) - 1):
        current = timelines[i]
        next_scene = timelines[i + 1]

        has_early_visual = any(
            e.action in ("SPAWN", "SHOW_TEXT", "SHOW_RESOURCE", "SHOW_LIST") and e.time < 1.0
            for e in next_scene.events
        )

        if has_early_visual:
            continue

        if current.events and current.events[-1].action == "FADE" and current.events[-1].target == "*":
            current.events.pop()

    return timelines


# --- File I/O ---

def _timeline_filename(section_name: str, scene_number: int) -> str:
    return f"timeline_{section_name}_scene_{scene_number}.txt"


def write_timeline(timeline: SceneTimeline, output_file: Path) -> None:
    lines = [
        f"SCENE {timeline.scene_number}",
        f"TYPE: {timeline.scene_type}",
        f"TOTAL_DURATION: {timeline.total_duration}s",
        f"VOICEOVER: {timeline.voiceover}",
        f"",
        f"TIMELINE:",
    ]

    for event in timeline.events:
        if event.action in ("SPAWN", "FADE", "SHOW_RESOURCE", "SHOW_TEXT", "SHOW_LIST", "HIGHLIGHT"):
            lines.append(f'  {event.time}s {event.action} "{event.target}" ({event.duration}s)')
        elif event.action == "ARROW":
            lines.append(f'  {event.time}s ARROW "{event.target}" ({event.duration}s)')
        elif event.action == "HOLD":
            lines.append(f"  {event.time}s HOLD ({event.duration}s)")

    output_file.write_text("\n".join(lines), encoding="utf-8")


# --- Processing ---

def process_storyboard_file(filepath: Path) -> None:
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    section_name = filepath.stem
    scenes = _parse_storyboard_file(filepath)

    timelines = []
    for scene in scenes:
        builder = SCENE_BUILDERS.get(scene["type"], _build_narration_only)
        timeline = builder(scene)
        timelines.append(timeline)

    timelines = _apply_carry_over(timelines)

    for timeline in timelines:
        filename = _timeline_filename(section_name, timeline.scene_number)
        output_file = TIMELINES_DIR / filename
        if output_file.exists():
            continue
        write_timeline(timeline, output_file)
        print(f"    {filename}")


def process_all_storyboards() -> None:
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(STORYBOARD_DIR.glob("section_*.txt")):
        process_storyboard_file(f)


# --- Watchdog ---

def start_watcher(executor=None):
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    from concurrent.futures import ThreadPoolExecutor

    STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()

    class StoryboardHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            filepath = Path(event.src_path)
            if filepath.suffix == ".txt" and filepath.stem.startswith("section_"):
                logger.info(f"[watchdog] New storyboard detected: {filepath.name}")
                _executor.submit(process_storyboard_file, filepath)

    handler = StoryboardHandler()
    observer = Observer()
    observer.schedule(handler, str(STORYBOARD_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer) -> None:
    observer.stop()
    observer.join()
