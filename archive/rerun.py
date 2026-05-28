"""
Rerun the pipeline on existing extracted sections (skip ingestion).

Same shared-executor watchdog cascade as main.py, but instead of running
`ingest()` on a PDF it kicks off the cascade by submitting every existing
section_*.txt to the group stage. Use this when you've already extracted
the PDF and want to rebuild downstream artifacts (timelines / render /
narration / assemble / parts) with updated code.

Sections are processed CONCURRENTLY through the shared thread pool — each
section's group → timeline → narrate flows in parallel with others' compile
→ render → assemble. Wall time is bounded by the slowest stage (manim render)
rather than the sum of all stages.

Usage:
    python rerun.py                  # process all sections (auto-detected from pipeline/sections/)
    python rerun.py --only 1,2,3,7   # process only the listed section IDs
    python rerun.py --range 4-20     # process a contiguous range
"""
import argparse
import re
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from pathlib import Path

from watchdog.observers import Observer

from src.utils import logger
from src.config.constants import (
    PIPELINE_THREAD_WORKERS,
    PIPELINE_PROCESS_WORKERS,
    GROUPING_SECTIONS_DIR as SECTIONS_DIR,
)
from src.scene_grouping.group import start_watcher as start_group_watcher, stop_watcher as stop_group_watcher
from src.scene_grouping.llm_grouper import process_section as group_process_section
from src.scene_grouping.llm_timeline import process_section as timeline_process_section
from src.config.constants import (
    GROUPING_CONTENT_GROUPS_DIR,
    GROUPING_TIMELINES_DIR,
    GROUPING_RENDER_DIR,
    GROUPING_MANIM_VIDEO_DIR,
    GROUPING_NARRATION_DIR,
    GROUPING_OUTPUT_DIR,
)
from src.icons.download import start_watcher as start_icons_watcher, stop_watcher as stop_icons_watcher
from src.compiler.compile import (
    start_watcher as start_compile_watcher,
    stop_watcher as stop_compile_watcher,
    compile_to_render_json,
)
from src.renderer.render import (
    start_watcher as start_render_watcher,
    stop_watcher as stop_render_watcher,
    render_manim,
)
from src.narration.narrate import (
    start_watcher as start_narrate_watcher,
    stop_watcher as stop_narrate_watcher,
    narrate_scene,
)
from src.assembler.assemble import (
    start_watcher as start_assemble_watcher,
    stop_watcher as stop_assemble_watcher,
    assemble_scene,
    concat_section,
)


def _list_section_files(only: set[int] | None = None) -> list[Path]:
    sections: list[tuple[int, Path]] = []
    for p in SECTIONS_DIR.glob("section_*.txt"):
        m = re.match(r"section_(\d+)\.txt", p.name)
        if not m:
            continue
        sid = int(m.group(1))
        if only is not None and sid not in only:
            continue
        sections.append((sid, p))
    sections.sort(key=lambda t: t[0])
    return [p for _, p in sections]


def _parse_only(only_arg: str | None, range_arg: str | None) -> set[int] | None:
    if only_arg:
        return {int(s.strip()) for s in only_arg.split(",") if s.strip()}
    if range_arg:
        m = re.match(r"(\d+)-(\d+)", range_arg)
        if not m:
            raise SystemExit(f"Invalid --range: {range_arg!r} (want e.g. '4-20')")
        lo, hi = int(m.group(1)), int(m.group(2))
        return set(range(lo, hi + 1))
    return None


def rerun(section_files: list[Path]) -> None:
    """Spin up every watcher, fan out group_process_section across the pool,
    then idle until the user kills it."""
    thread_pool = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)
    process_pool = ProcessPoolExecutor(max_workers=PIPELINE_PROCESS_WORKERS)

    logger.info(f"Shared thread pool: {PIPELINE_THREAD_WORKERS} workers")
    logger.info(f"Shared process pool: {PIPELINE_PROCESS_WORKERS} workers")
    logger.info("Starting watchers in reverse-cascade order...")

    # Start downstream watchers first so they're already waiting when files appear.
    assemble_observer = start_assemble_watcher(executor=thread_pool)

    # compile + narrate both watch pipeline/groups/timelines/. fsevents on Mac
    # crashes if two Observer instances watch the same path simultaneously, so
    # share a single Observer between them.
    #
    # Compile uses the thread pool, NOT the process pool — the watcher submits
    # a bound method to its executor, and ProcessPoolExecutor would have to
    # pickle the handler instance (including its executor reference), which
    # fails silently. Compile work is fast (Lark parse + transform, ms-level)
    # so process isolation isn't needed anyway.
    timelines_observer = Observer()
    start_compile_watcher(executor=thread_pool, observer=timelines_observer)
    start_narrate_watcher(executor=thread_pool, observer=timelines_observer)
    timelines_observer.start()

    render_observer = start_render_watcher(executor=thread_pool)
    icons_observer = start_icons_watcher(executor=thread_pool)
    group_observers = start_group_watcher(executor=thread_pool)

    logger.info(f"All watchers running. Triggering group stage on {len(section_files)} sections...")

    # Fan the group stage out across the thread pool. Each section's
    # processing writes a content_groups file, which the timeline watcher
    # picks up and runs in parallel. The cascade unfolds from there.
    futures = {
        thread_pool.submit(group_process_section, f): f
        for f in section_files
    }
    completed = 0
    for future in as_completed(futures):
        section_file = futures[future]
        try:
            future.result()
            completed += 1
            logger.info(f"[group {completed}/{len(section_files)}] {section_file.name} → content_groups written")
        except Exception as e:
            completed += 1
            logger.error(f"[group {completed}/{len(section_files)}] {section_file.name} FAILED: {e}")

    # If content_groups already existed on disk (cached run), the group stage
    # is a no-op and the timeline watcher never fired (no file-creation event).
    # Manually fan out timeline processing for those — each timeline_process_section
    # writes new timeline files, which DO trigger the compile + narrate watchers
    # and the rest of the cascade flows from there.
    content_groups_files: list[Path] = []
    for section_file in section_files:
        cg_file = GROUPING_CONTENT_GROUPS_DIR / section_file.name
        if cg_file.exists():
            content_groups_files.append(cg_file)
    logger.info(f"Triggering timeline stage on {len(content_groups_files)} cached content_groups...")

    timeline_futures = {
        thread_pool.submit(timeline_process_section, f): f
        for f in content_groups_files
    }
    completed = 0
    for future in as_completed(timeline_futures):
        cg_file = timeline_futures[future]
        try:
            future.result()
            completed += 1
            logger.info(f"[timeline {completed}/{len(content_groups_files)}] {cg_file.name} done")
        except Exception as e:
            completed += 1
            logger.error(f"[timeline {completed}/{len(content_groups_files)}] {cg_file.name} FAILED: {e}")

    logger.info("All timelines submitted. Compile + narrate + render + assemble watchers are cascading.")
    logger.info("Now fanning out downstream stages for any cached pipeline state...")

    # Watchers only fire on NEW file events. If timeline/compile/render/narration
    # files already exist from a previous run, no events fire, the cascade stalls.
    # So we ALSO submit each downstream stage explicitly for any backlog. Each
    # stage is idempotent (skips outputs that already exist).
    section_ids = sorted({int(re.match(r"section_(\d+)", f.stem).group(1))
                          for f in section_files
                          if re.match(r"section_(\d+)", f.stem)})

    # Compile: every timeline_*.txt missing its render.json
    compile_pending: list[Path] = []
    for sid in section_ids:
        for tl in GROUPING_TIMELINES_DIR.glob(f"timeline_section_{sid}_scene_*.txt"):
            rj = GROUPING_RENDER_DIR / f"{tl.stem}.render.json"
            if not rj.exists():
                compile_pending.append(tl)
    if compile_pending:
        logger.info(f"Compile fan-out: {len(compile_pending)} timelines need compiling")
        for f in compile_pending:
            thread_pool.submit(compile_to_render_json, f)

    # Narrate: every timeline_*.txt missing its narration .wav
    narrate_pending: list[Path] = []
    for sid in section_ids:
        for tl in GROUPING_TIMELINES_DIR.glob(f"timeline_section_{sid}_scene_*.txt"):
            wav = GROUPING_NARRATION_DIR / f"{tl.stem}.wav"
            if not wav.exists():
                narrate_pending.append(tl)
    if narrate_pending:
        logger.info(f"Narrate fan-out: {len(narrate_pending)} timelines need narration")
        for f in narrate_pending:
            thread_pool.submit(narrate_scene, f)

    # Render: every render.json missing its manim mp4 (serialised by lock internally)
    render_pending: list[tuple[Path, str]] = []
    for sid in section_ids:
        for rj in GROUPING_RENDER_DIR.glob(f"timeline_section_{sid}_scene_*.render.json"):
            scene_name = rj.stem.removesuffix(".render")
            mp4 = GROUPING_MANIM_VIDEO_DIR / f"{scene_name}.mp4"
            if not mp4.exists():
                render_pending.append((rj, scene_name))
    if render_pending:
        logger.info(f"Render fan-out: {len(render_pending)} scenes need rendering")
        render_futures = [
            thread_pool.submit(render_manim, rj, name)
            for rj, name in render_pending
        ]
        for fut in as_completed(render_futures):
            try:
                fut.result()
            except Exception as e:
                logger.error(f"Render fan-out task failed: {e}")
        logger.info("Render fan-out complete.")

    # Assemble + concat: for each section, try to assemble each scene and then concat
    logger.info("Assemble + concat fan-out for sections...")
    for sid in section_ids:
        # Each scene
        for tl in sorted(GROUPING_TIMELINES_DIR.glob(f"timeline_section_{sid}_scene_*.txt")):
            try:
                assemble_scene(tl.stem)
            except Exception as e:
                logger.error(f"assemble_scene({tl.stem}) failed: {e}")
        # Section concat
        section_mp4 = GROUPING_OUTPUT_DIR / f"section_{sid}.mp4"
        if not section_mp4.exists():
            try:
                concat_section(f"section_{sid}")
            except Exception as e:
                logger.error(f"concat_section(section_{sid}) failed: {e}")

    logger.info("Done. Output mp4s are in pipeline/output/.")
    logger.info("Press Ctrl+C to stop the watchers (or they keep running for any new events).")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        stop_group_watcher(group_observers)
        stop_icons_watcher(icons_observer)
        stop_render_watcher(render_observer)
        # Single shared observer for timelines/ — stop once
        timelines_observer.stop()
        timelines_observer.join()
        stop_assemble_watcher(assemble_observer)
        thread_pool.shutdown(wait=True)
        process_pool.shutdown(wait=True)
        logger.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rerun the pipeline on existing extracted sections (skip ingest)")
    parser.add_argument("--only", type=str, help="Comma-separated section IDs (e.g., '1,2,3,7')")
    parser.add_argument("--range", type=str, help="Section range (e.g., '4-20')")
    args = parser.parse_args()

    only = _parse_only(args.only, args.range)
    section_files = _list_section_files(only=only)
    if not section_files:
        print("No section files matched. Check pipeline/sections/ and your --only/--range filters.")
        raise SystemExit(1)

    rerun(section_files)
