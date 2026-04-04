"""
Scene grouping orchestrator.

Calls each step of the scene grouping pipeline:
1. Starts timeline watcher (watchdog) on pipeline/groups/scene_groups/
2. LLM grouper writes scene group files
3. Watcher detects new files → generates timeline files concurrently
"""
from src.scene_grouping.llm_grouper import (
    BACKENDS,
    HANDLERS,
    collect_pending_files,
    output_dir,
    output_path,
)
from src.scene_grouping.timeline import start_watcher, stop_watcher

from pathlib import Path


def group_all(backend: str = "gemini") -> None:
    output_dir().mkdir(parents=True, exist_ok=True)

    pending = collect_pending_files()
    if not pending:
        print("All section files already have scene groups. Nothing to do.")
        return

    print(f"Found {len(pending)} pending section files.")

    # Start timeline watcher before LLM grouper writes files
    observer = start_watcher()
    print("Timeline watcher started.")

    handler = HANDLERS.get(backend)
    if handler is None:
        stop_watcher(observer)
        raise ValueError(f"No handler for backend: {backend}. Choose from: {list(HANDLERS.keys())}")

    try:
        handler(pending, backend)
    finally:
        # Give watcher a moment to process remaining files, then stop
        import time
        time.sleep(2)
        stop_watcher(observer)
        print("Timeline watcher stopped.")

    print("Done.")


if __name__ == "__main__":
    # Usage:
    #   python -m src.scene_grouping.group --all --backend ollama
    #   python -m src.scene_grouping.group pipeline/sections/section_107.txt --backend ollama
    import sys
    import argparse

    from src.scene_grouping.llm_grouper import group_section_file
    from src.scene_grouping.timeline import process_scene_group_file_threaded

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

        # Single file: group then timeline
        result = group_section_file(path, backend=args.backend)
        out = output_path(path)
        output_dir().mkdir(parents=True, exist_ok=True)
        out.write_text(result, encoding="utf-8")
        print(f"Wrote: {out}")

        process_scene_group_file_threaded(out)
    else:
        parser.print_help()
