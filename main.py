"""
VidJourney main pipeline.

Runs ingestion, then starts all downstream watchers with shared executor pools.
The watchers cascade automatically:

  ingest → sections/
    → group.py (watches sections/) → storyboard/ → timelines/
      → compile.py (watches timelines/) → scene_files/ → render/
        → render.py (watches render/) → manim video
      → narrate.py (watches timelines/) → narration audio
        → assemble.py (watches narration/) → final output

Concurrency is bounded by two shared pools:
  - ThreadPoolExecutor for I/O-bound tasks (LLM, manim, TTS, ffmpeg)
  - ProcessPoolExecutor for CPU-bound tasks (compilation, keyword extraction)
"""
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from pathlib import Path

from src.utils import logger
from src.config.constants import PIPELINE_THREAD_WORKERS, PIPELINE_PROCESS_WORKERS
from src.ingestion.ingest_pdf import ingest
from src.scene_grouping.group import start_watcher as start_group_watcher, stop_watcher as stop_group_watcher
from src.compiler.compile import start_watcher as start_compile_watcher, stop_watcher as stop_compile_watcher
from src.renderer.render import start_watcher as start_render_watcher, stop_watcher as stop_render_watcher
from src.narration.narrate import start_watcher as start_narrate_watcher, stop_watcher as stop_narrate_watcher
from src.assembler.assemble import start_watcher as start_assemble_watcher, stop_watcher as stop_assemble_watcher


def main(pdf_path: Path, backend: str = "ollama") -> None:
    # Create shared executor pools — all modules draw from these
    thread_pool = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)
    process_pool = ProcessPoolExecutor(max_workers=PIPELINE_PROCESS_WORKERS)

    logger.info(f"Shared thread pool: {PIPELINE_THREAD_WORKERS} workers")
    logger.info(f"Shared process pool: {PIPELINE_PROCESS_WORKERS} workers")
    logger.info("Starting watchers...")

    # Start all watchers (order: downstream first so they're ready when events arrive)
    assemble_observer = start_assemble_watcher(executor=thread_pool)
    logger.info("Assembler watcher started (thread pool — I/O: ffmpeg)")

    narrate_observer = start_narrate_watcher(executor=thread_pool)
    logger.info("Narration watcher started (thread pool — I/O: TTS)")

    render_observer = start_render_watcher(executor=thread_pool)
    logger.info("Renderer watcher started (thread pool — I/O: manim subprocess)")

    compile_observer = start_compile_watcher(executor=process_pool)
    logger.info("Compiler watcher started (process pool — CPU: DSL parsing)")

    group_observers = start_group_watcher(backend=backend, executor=thread_pool)
    logger.info("Scene grouping watcher started (thread pool — I/O: LLM calls)")

    logger.info(f"All watchers running. Starting ingestion of {pdf_path}...")

    try:
        ingest(pdf_path)
        logger.info("Ingestion complete. Watchers are processing...")
        logger.info("Press Ctrl+C to stop.")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        stop_group_watcher(group_observers)
        logger.info("Scene grouping watcher stopped.")

        stop_compile_watcher(compile_observer)
        logger.info("Compiler watcher stopped.")

        stop_render_watcher(render_observer)
        logger.info("Renderer watcher stopped.")

        stop_narrate_watcher(narrate_observer)
        logger.info("Narration watcher stopped.")

        stop_assemble_watcher(assemble_observer)
        logger.info("Assembler watcher stopped.")

        thread_pool.shutdown(wait=True)
        logger.info("Thread pool shut down.")

        process_pool.shutdown(wait=True)
        logger.info("Process pool shut down.")

    logger.info("Done.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VidJourney — PDF to narrated video")
    parser.add_argument("pdf_path", type=str, help="Path to the PDF file")
    parser.add_argument("--backend", type=str, default="ollama", choices=["ollama", "gemini"])
    args = parser.parse_args()

    path = Path(args.pdf_path)
    if not path.exists():
        print(f"File not found: {path}")
        exit(1)

    main(path, backend=args.backend)
