"""
LLM-based timeline generator.

Reads content groups and produces timeline scene files. The LLM is only
called for paragraph groups with multiple resources — it decides which
resource to display alongside which sentences. All other cases are
deterministic.

Usage:
    python -m src.scene_grouping.llm_timeline pipeline/groups/content_groups/section_10.txt
    python -m src.scene_grouping.llm_timeline --all
"""
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from src.utils import logger, timer
from src.config.constants import (
    GROUPING_CONTENT_GROUPS_DIR,
    GROUPING_TIMELINES_DIR,
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
)
from src.scene_grouping.llm_grouper import (
    ContentGroup,
    deserialize_groups,
    RESOURCE_KINDS,
)
from src.scene_grouping.llm_entity import extract as extract_entities, EntityResult


CONTENT_GROUPS_DIR = GROUPING_CONTENT_GROUPS_DIR
TIMELINES_DIR = GROUPING_TIMELINES_DIR

WPM = 150.0
MIN_DURATION = 4.0
SPAWN_DURATION = 0.5
FADE_DURATION = 0.5


# --- Data structures ---

@dataclass
class TimelineEvent:
    time: float
    action: str      # SHOW_RESOURCE, SHOW_TEXT, FADE, HOLD
    target: str      # resource path, text content, "*", or ""
    duration: float


@dataclass
class Scene:
    scene_id: int
    voiceover: str
    events: list[TimelineEvent] = field(default_factory=list)
    duration: float = 0.0


# --- Utilities ---

def _duration(text: str) -> float:
    words = len(text.split())
    seconds = (words / WPM) * 60.0
    return round(max(MIN_DURATION, seconds), 1)


def _split_sentences(text: str) -> list[str]:
    text = text.replace("\u00ad", "").replace("\u2010\n", "").replace("\u2010", "")
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [s.strip() for s in sentences if s.strip()]


def _strip_bullet(text: str) -> str:
    return re.sub(r"^[•·\-*]\s*", "", text).strip()


# --- LLM call ---

PROMPT = """\
Given a paragraph and its associated resources, split the paragraph text into \
consecutive scene segments. Each segment will be narrated while displaying one \
specific resource.

Paragraph:
"{paragraph}"

Resources:
{resources}

Output valid JSON only:
{{"scenes": [{{"text": "<exact consecutive sentences from paragraph>", "resource_index": <int>}}, ...]}}

Rules:
- Each scene's text must be exact consecutive sentences from the paragraph.
- Together, all scene texts must cover the entire paragraph. Do not drop or add text.
- Each scene gets exactly one resource_index to display.
- A resource can appear in multiple consecutive scenes if needed.
- If the paragraph only references one resource, put all text in one scene with that resource."""


def _call_ollama(prompt: str) -> str:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": 16384, "temperature": 0},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _build_resource_prompt(group: ContentGroup) -> str:
    cap_map = group.caption_for_resource
    resources = group.resources
    lines = []
    for i, res in enumerate(resources):
        caption = cap_map.get(res.text, "(no caption)")
        lines.append(f"[{i}] {res.kind} {res.text} — \"{caption}\"")
    return PROMPT.format(
        paragraph=group.anchor.text,
        resources="\n".join(lines),
    )


def _coerce_resource_index(raw, default: int = 0) -> int:
    """Coerce LLM-returned resource_index to int, accepting int, str, or list of int."""
    if raw is None:
        return default
    if isinstance(raw, list):
        return _coerce_resource_index(raw[0], default) if raw else default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _parse_llm_scenes(response_text: str, resources: list) -> list[dict]:
    data = json.loads(response_text)
    scenes = data.get("scenes", data)
    result = []
    for s in scenes:
        text = s.get("text", "").strip()
        r_idx = _coerce_resource_index(s.get("resource_index"))
        if text and 0 <= r_idx < len(resources):
            result.append({"text": text, "resource_index": r_idx})
    return result


def _structural_resource_fallback(paragraph_text: str, resources: list) -> list[dict]:
    """Split sentences evenly across resources."""
    sentences = _split_sentences(paragraph_text)
    if not sentences:
        return [{"text": paragraph_text or "", "resource_index": 0}]

    chunk_size = max(1, math.ceil(len(sentences) / len(resources)))
    result = []
    for i, res_idx in enumerate(range(len(resources))):
        chunk = sentences[i * chunk_size:(i + 1) * chunk_size]
        if chunk:
            result.append({"text": " ".join(chunk), "resource_index": res_idx})

    # Any remaining sentences go to the last resource
    remaining = sentences[len(resources) * chunk_size:]
    if remaining and result:
        result[-1]["text"] += " " + " ".join(remaining)

    return result


# --- Scene builders ---

def _build_heading_scenes(group: ContentGroup, scene_id: int) -> list[Scene]:
    text = group.anchor.text
    dur = _duration(text)
    return [Scene(
        scene_id=scene_id,
        voiceover=text,
        duration=dur,
        events=[
            TimelineEvent(time=0.0, action="SHOW_TEXT", target=text, duration=round(dur - FADE_DURATION, 1)),
            TimelineEvent(time=round(dur - FADE_DURATION, 1), action="FADE", target="*", duration=FADE_DURATION),
        ],
    )]


def _build_paragraph_resource_scenes(group: ContentGroup, scene_id: int) -> list[Scene]:
    resources = group.resources

    # Single resource — no LLM needed, all sentences get that resource
    if len(resources) == 1:
        segments = [{"text": group.anchor.text, "resource_index": 0}]
    else:
        # Multiple resources — ask the LLM
        segments = None
        for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
            try:
                prompt = _build_resource_prompt(group)
                response_text = _call_ollama(prompt)
                segments = _parse_llm_scenes(response_text, resources)
                if segments:
                    logger.info(f"LLM split paragraph into {len(segments)} segments")
                    break
            except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning(f"LLM timeline attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

        if not segments:
            logger.warning("LLM timeline failed, using structural fallback")
            segments = _structural_resource_fallback(group.anchor.text, resources)

    scenes = []
    for seg in segments:
        text = seg["text"]
        res = resources[seg["resource_index"]]
        dur = _duration(text)
        scenes.append(Scene(
            scene_id=scene_id,
            voiceover=text,
            duration=dur,
            events=[
                TimelineEvent(time=0.0, action="SHOW_RESOURCE", target=res.text, duration=SPAWN_DURATION),
                TimelineEvent(time=round(dur - FADE_DURATION, 1), action="FADE", target="*", duration=FADE_DURATION),
            ],
        ))
        scene_id += 1

    return scenes


def _build_paragraph_list_scenes(group: ContentGroup, scene_id: int) -> list[Scene]:
    scenes = []

    # Intro paragraph scene
    intro_text = group.anchor.text
    dur = _duration(intro_text)
    scenes.append(Scene(
        scene_id=scene_id,
        voiceover=intro_text,
        duration=dur,
        events=[TimelineEvent(time=0.0, action="HOLD", target="", duration=dur)],
    ))
    scene_id += 1

    # One scene per list item
    for item_el in group.list_items:
        text = _strip_bullet(item_el.text)
        dur = _duration(text)
        scenes.append(Scene(
            scene_id=scene_id,
            voiceover=text,
            duration=dur,
            events=[TimelineEvent(time=0.0, action="HOLD", target="", duration=dur)],
        ))
        scene_id += 1

    return scenes


def _build_paragraph_blank_scenes(group: ContentGroup, scene_id: int) -> list[Scene]:
    """Build scenes for a paragraph with no resources/list items.

    Collapses the paragraph into ONE scene and uses the LLM entity extractor
    to populate the visuals — quote text, entity icons + arrows, or HOLD fallback.
    """
    text = group.anchor.text.strip()
    if not text:
        return []

    dur = _duration(text)
    result = extract_entities(text)
    events = _events_for_entity_result(result, dur)

    return [Scene(
        scene_id=scene_id,
        voiceover=text,
        duration=dur,
        events=events,
    )]


def _events_for_entity_result(result: EntityResult, dur: float) -> list[TimelineEvent]:
    """Build TIMELINE events from an entity extraction result."""
    fade_at = round(dur - FADE_DURATION, 1)

    if result.type == "quote":
        events = []
        # Show the quote text immediately
        attribution_at = round(dur * 0.7, 1) if result.attribution else fade_at
        events.append(TimelineEvent(
            time=0.0,
            action="SHOW_TEXT",
            target=result.text,
            duration=attribution_at,
        ))
        if result.attribution:
            events.append(TimelineEvent(
                time=attribution_at,
                action="SHOW_TEXT",
                target=f"— {result.attribution}",
                duration=round(fade_at - attribution_at, 1),
            ))
        events.append(TimelineEvent(time=fade_at, action="FADE", target="*", duration=FADE_DURATION))
        return events

    if result.type == "entities":
        events = []
        # Spawn entities staggered over the first 20% of the scene
        spawn_window = max(SPAWN_DURATION, dur * 0.2)
        gap = spawn_window / max(len(result.spawns), 1)
        for i, entity in enumerate(result.spawns):
            events.append(TimelineEvent(
                time=round(i * gap, 1),
                action="SPAWN",
                target=entity,
                duration=SPAWN_DURATION,
            ))
        # Draw arrows after spawns complete
        arrow_start = round(spawn_window, 1)
        for i, arrow in enumerate(result.arrows):
            target = f'{arrow["from"]} --[{arrow["verb"]}]--> {arrow["to"]}'
            events.append(TimelineEvent(
                time=round(arrow_start + i * 0.5, 1),
                action="ARROW",
                target=target,
                duration=1.5,
            ))
        events.append(TimelineEvent(time=fade_at, action="FADE", target="*", duration=FADE_DURATION))
        return events

    # abstract → blank screen
    return [TimelineEvent(time=0.0, action="HOLD", target="", duration=dur)]


def _build_standalone_resource_scenes(group: ContentGroup, scene_id: int) -> list[Scene]:
    resource = group.anchor
    captions = group.captions
    caption = captions[0].text if captions else ""
    dur = _duration(caption) if caption else MIN_DURATION
    return [Scene(
        scene_id=scene_id,
        voiceover=caption,
        duration=dur,
        events=[
            TimelineEvent(time=0.0, action="SHOW_RESOURCE", target=resource.text, duration=SPAWN_DURATION),
            TimelineEvent(time=round(dur - FADE_DURATION, 1), action="FADE", target="*", duration=FADE_DURATION),
        ],
    )]


def _build_standalone_list_scenes(group: ContentGroup, scene_id: int) -> list[Scene]:
    scenes = []
    for item_el in group.list_items:
        text = _strip_bullet(item_el.text)
        dur = _duration(text)
        scenes.append(Scene(
            scene_id=scene_id,
            voiceover=text,
            duration=dur,
            events=[TimelineEvent(time=0.0, action="HOLD", target="", duration=dur)],
        ))
        scene_id += 1
    return scenes


# --- Dispatcher ---

def build_timeline(groups: list[ContentGroup]) -> list[Scene]:
    """Convert content groups into a flat list of scenes."""
    scenes: list[Scene] = []
    scene_id = 1

    for group in groups:
        if group.kind == "heading":
            new_scenes = _build_heading_scenes(group, scene_id)
        elif group.kind == "paragraph":
            if group.list_items:
                new_scenes = _build_paragraph_list_scenes(group, scene_id)
            elif group.resources:
                new_scenes = _build_paragraph_resource_scenes(group, scene_id)
            else:
                new_scenes = _build_paragraph_blank_scenes(group, scene_id)
        elif group.kind in ("image", "code_block", "table"):
            new_scenes = _build_standalone_resource_scenes(group, scene_id)
        elif group.kind == "list":
            new_scenes = _build_standalone_list_scenes(group, scene_id)
        else:
            continue

        scenes.extend(new_scenes)
        scene_id = scenes[-1].scene_id + 1 if scenes else scene_id

    return scenes


# --- Serialization ---

def serialize_scene(scene: Scene) -> str:
    """Render a Scene to the timeline text format consumed by compiler and narrator."""
    lines = [
        f"SCENE {scene.scene_id}",
        f"TOTAL_DURATION: {scene.duration}s",
        f"VOICEOVER: {scene.voiceover}",
        "",
        "TIMELINE:",
    ]
    for ev in scene.events:
        if ev.target:
            lines.append(f'  {ev.time}s {ev.action} "{ev.target}" ({ev.duration}s)')
        else:
            lines.append(f"  {ev.time}s {ev.action} ({ev.duration}s)")
    return "\n".join(lines)


# --- File I/O ---

def _has_timelines(section_name: str) -> bool:
    return bool(list(TIMELINES_DIR.glob(f"timeline_{section_name}_scene_*.txt")))


def timeline_section_file(content_groups_file: Path) -> list[Path]:
    """Read content groups, build timeline, write scene files. Returns written paths."""
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    section_name = content_groups_file.stem
    content = content_groups_file.read_text(encoding="utf-8", errors="replace")
    groups = deserialize_groups(content)
    scenes = build_timeline(groups)

    written: list[Path] = []
    for scene in scenes:
        filename = f"timeline_{section_name}_scene_{scene.scene_id}.txt"
        out = TIMELINES_DIR / filename
        if out.exists():
            continue
        out.write_text(serialize_scene(scene), encoding="utf-8")
        written.append(out)

    return written


@timer(label="Timeline section")
def process_section(content_groups_file: Path) -> list[Path]:
    """Generate timeline scenes for a single section."""
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    section_name = content_groups_file.stem
    if _has_timelines(section_name):
        logger.info(f"Already has timelines: {section_name}")
        return []

    logger.info(f"Generating timelines for {content_groups_file.name}")
    written = timeline_section_file(content_groups_file)
    logger.info(f"Wrote {len(written)} timeline files for {section_name}")
    return written


# --- Watchdog ---

def start_watcher(executor=None) -> "Observer":
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    from concurrent.futures import ThreadPoolExecutor

    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()

    class ContentGroupHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            filepath = Path(event.src_path)
            if filepath.suffix == ".txt" and filepath.stem.startswith("section_"):
                if not _has_timelines(filepath.stem):
                    logger.info(f"[watchdog] New content groups detected: {filepath.name}")
                    _executor.submit(_safe_process, filepath)

    def _safe_process(filepath: Path):
        try:
            process_section(filepath)
        except Exception as e:
            logger.error(f"Timeline generation failed: {filepath.name} — {e}")

    handler = ContentGroupHandler()
    observer = Observer()
    observer.schedule(handler, str(CONTENT_GROUPS_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer) -> None:
    observer.stop()
    observer.join()


# --- Batch processing ---

def process_all(executor: "ThreadPoolExecutor") -> None:
    """Process all pending sections concurrently using the shared executor."""
    from concurrent.futures import as_completed

    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    pending = [
        f for f in sorted(CONTENT_GROUPS_DIR.glob("section_*.txt"))
        if not _has_timelines(f.stem)
    ]
    if not pending:
        logger.info("All sections already have timelines. Nothing to do.")
        return

    logger.info(f"Found {len(pending)} pending sections. Processing concurrently...")

    futures = {executor.submit(process_section, f): f for f in pending}
    succeeded = failed = 0
    for future in as_completed(futures):
        group_file = futures[future]
        try:
            future.result()
            succeeded += 1
            logger.info(f"[{succeeded + failed}/{len(pending)}] Done: {group_file.name}")
        except Exception as e:
            failed += 1
            logger.error(f"[{succeeded + failed}/{len(pending)}] FAILED: {group_file.name} — {e}")

    logger.info(f"Completed: {succeeded} succeeded, {failed} failed out of {len(pending)}")


# --- CLI ---

def _print_scenes(scenes: list[Scene]) -> None:
    for scene in scenes:
        print(serialize_scene(scene))
        print()


if __name__ == "__main__":
    import sys
    import time
    import argparse
    from concurrent.futures import ThreadPoolExecutor

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="LLM-based timeline generator")
    parser.add_argument("content_groups_file", type=str, nargs="?", help="Path to a content groups file")
    parser.add_argument("--all", action="store_true", help="Process all pending (concurrent)")
    parser.add_argument("--watch", action="store_true", help="Watch content groups dir for new files")
    args = parser.parse_args()

    _executor = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)

    if args.watch:
        logger.info(f"Watching {CONTENT_GROUPS_DIR} for new content groups...")
        observer = start_watcher(executor=_executor)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_watcher(observer)
            _executor.shutdown(wait=True)
            logger.info("Stopped.")
    elif args.all:
        process_all(executor=_executor)
        _executor.shutdown(wait=True)
    elif args.content_groups_file:
        path = Path(args.content_groups_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        content = path.read_text(encoding="utf-8", errors="replace")
        groups = deserialize_groups(content)
        scenes = build_timeline(groups)
        _print_scenes(scenes)
    else:
        parser.print_help()
