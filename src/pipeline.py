"""
Pipeline orchestrator — the watchdog cascade.

Runs the whole thing from one command: ingest a PDF, then let each stage fire
as its input files land, all sharing one bounded :class:`Scheduler` so the
machine never oversubscribes.

    python -m src.pipeline <book.pdf>              # full cascade (with review gate hop)
    python -m src.pipeline <book.pdf> --no-gate    # skip the gate; render straight off content_groups
    python -m src.pipeline <book.pdf> --workers N  # pool size for this run

Cascade (file boundaries)::

    ingest(pdf)                     -> pipeline/sections/section_*.txt
      grouping   (watch sections/)      -> pipeline/groups/content_groups/section_*.txt
      gate       (watch content_groups) -> pipeline/groups/approved/section_*.txt   [auto-pass]
      render     (watch approved/)      -> pipeline/scroll/output/section_*_raster.mp4
      assemble   (watch output/)        -> pipeline/scroll/parts/*.mp4
      describe   (watch parts/)         -> pipeline/descriptions/part_*.md

Publish stays manual (``python -m src.publisher.upload``), and interactive
review (``python -m src.grouping.review_gate <section> --review``) is an
out-of-band human action — the cascade's gate hop only auto-passes, so it never
blocks.
"""
from __future__ import annotations

import argparse
import time

from src.utils import logger
from src.scheduler import get_scheduler
from src.ingestion.ingest_pdf import ingest
from src.grouping import llm_grouper
from src.grouping import review_gate
from src.render import build_raster
from src.assembler import build_video
from src.publisher import describe
from src import stage_cli
from pathlib import Path


def run(pdf_path: Path, no_gate: bool = False) -> None:
    sched = get_scheduler()
    observers = []

    # Grouping: sections/ -> content_groups/
    observers.append(llm_grouper.start_watcher(sched.io))

    # Gate: content_groups/ -> approved/ (auto-pass in the cascade).
    if not no_gate:
        observers.append(review_gate.start_watcher())
    else:
        # Skip the approved/ hop — render watches content_groups directly
        # (build_raster._render_input also falls back to content_groups).
        build_raster.STAGE.watch_dir = build_raster.FALLBACK_DIR

    # Render: approved/ (or content_groups/ when --no-gate) -> output/
    observers.append(stage_cli.watch_stage(build_raster.STAGE, sched))

    # Assemble: output/ -> parts/  (repacks on each new section mp4)
    observers.append(stage_cli.watch_stage(build_video.STAGE, sched))

    # Describe: parts/ -> descriptions/  (regenerates part_*.md as parts land)
    observers.append(stage_cli.watch_stage(describe.STAGE, sched))

    gate_note = "no gate (content_groups→render)" if no_gate else "gate auto-pass"
    logger.info(f"Cascade live: ingest → group → [{gate_note}] → render → assemble → describe")

    # Ingest fills sections/, which trips the grouping watcher and cascades down.
    ingest(pdf_path)
    logger.info("Ingest complete; cascade draining. Ctrl-C to stop when idle.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping watchers…")
    finally:
        for obs in observers:
            obs.stop()
        for obs in observers:
            obs.join()
        sched.shutdown(wait=True)
        logger.info("Pipeline stopped.")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Run the full PDF→parts cascade")
    ap.add_argument("pdf", type=Path, help="path to the source PDF")
    ap.add_argument("--no-gate", dest="no_gate", action="store_true",
                    help="skip the review-gate hop; render straight off content_groups")
    ap.add_argument("--workers", type=int, default=None, help="pool size for this run (cpu + io)")
    ap.add_argument("--io-workers", type=int, default=None, help="override just the io pool size")
    args = ap.parse_args(argv)

    # First get_scheduler call fixes pool sizes.
    get_scheduler(
        cpu_workers=args.workers,
        io_workers=args.io_workers if args.io_workers is not None else args.workers,
    )
    run(args.pdf, no_gate=args.no_gate)


if __name__ == "__main__":
    main()
