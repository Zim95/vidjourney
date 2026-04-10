"""
Scene grouping orchestrator.

Pipeline:
1. Start DSL compiler watcher on pipeline/groups/timelines/
2. Start timeline watcher on pipeline/groups/scene_groups/
3. LLM grouper writes scene group files
4. Timeline watcher detects new files → generates timeline files
5. DSL compiler watcher detects new timeline files → generates .scene files

scene_groups/ → (watchdog) → timelines/ → (watchdog) → scenes/
"""
from src.scene_grouping.llm_grouper import (
    BACKENDS,
    HANDLERS,
    collect_pending_files,
    output_dir,
    output_path,
)
from src.scene_grouping import timeline
from src.scene_grouping import dsl_compiler

from pathlib import Path


def group_all(backend: str = "gemini") -> None:
    output_dir().mkdir(parents=True, exist_ok=True)

    pending = collect_pending_files()
    if not pending:
        print("All section files already have scene groups. Nothing to do.")
        return

    print(f"Found {len(pending)} pending section files.")

    # Start watchers: DSL compiler watches timelines, timeline watches scene_groups.
    # Order matters: DSL watcher first so it's ready when timelines start arriving.
    dsl_observer = dsl_compiler.start_watcher()
    print("DSL compiler watcher started.")

    timeline_observer = timeline.start_watcher()
    print("Timeline watcher started.")

    handler = HANDLERS.get(backend)
    if handler is None:
        timeline.stop_watcher(timeline_observer)
        dsl_compiler.stop_watcher(dsl_observer)
        raise ValueError(f"No handler for backend: {backend}. Choose from: {list(HANDLERS.keys())}")

    try:
        handler(pending, backend)
    finally:
        import time
        time.sleep(3)
        timeline.stop_watcher(timeline_observer)
        print("Timeline watcher stopped.")
        time.sleep(1)
        dsl_compiler.stop_watcher(dsl_observer)
        print("DSL compiler watcher stopped.")

    print("Done.")


if __name__ == "__main__":
    # Usage:
    #   python -m src.scene_grouping.group --all --backend ollama
    #   python -m src.scene_grouping.group pipeline/sections/section_107.txt --backend ollama
    import sys
    import argparse

    from src.scene_grouping.llm_grouper import group_section_file
    from src.scene_grouping.timeline import process_scene_group_file_threaded
    from src.scene_grouping.dsl_compiler import process_timeline_file

    parser = argparse.ArgumentParser(description="Scene grouping pipeline")
    parser.add_argument("section_file", type=str, nargs="?", help="Path to a section file")
    parser.add_argument("--backend", type=str, default="gemini", choices=list(BACKENDS.keys()))
    parser.add_argument("--all", action="store_true", help="Process all pending section files")
    args = parser.parse_args()

    if args.all:
        group_all(backend=args.backend)
    elif args.section_file:
        path = Path(args.section_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)

        # Single file: full pipeline
        result = group_section_file(path, backend=args.backend)
        out = output_path(path)
        output_dir().mkdir(parents=True, exist_ok=True)
        out.write_text(result, encoding="utf-8")
        print(f"Scene group: {out}")

        process_scene_group_file_threaded(out)

        # Compile timelines to DSL
        from src.config.constants import GROUPING_TIMELINES_DIR
        for timeline_file in sorted(GROUPING_TIMELINES_DIR.glob(f"timeline_{path.stem}_*.txt")):
            process_timeline_file(timeline_file)
    else:
        parser.print_help()
