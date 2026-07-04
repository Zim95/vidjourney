"""
Shared dual-trigger CLI for pipeline stages.

Every stage exposes the same terminal contract:

    python -m <stage> <item>        # one item (debug)
    python -m <stage> --all         # all pending, fanned across the shared pool
    python -m <stage> --watch       # watchdog: fire process_one as inputs land
    python -m <stage> --workers N   # pool size for this run

A stage is described by a :class:`Stage` and its ``__main__`` is one line —
``run_stage(STAGE)``. The only difference between ``--all`` and ``--watch`` is
*when* ``process_one`` runs; both submit to the same shared Scheduler, so a
stage run standalone after the previous one finishes yields the same
parallelism (and same artifacts) as the watchdog cascade.

Lives at ``src/stage_cli.py`` (not ``src/utils/stage_cli.py``) because
``src/utils.py`` is a module, so ``src/utils/`` can't also be a package.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.utils import logger
from src.scheduler import Scheduler, get_scheduler


@dataclass
class Stage:
    """Declarative description of a pipeline stage for :func:`run_stage`.

    Per-item stages set ``process_one`` + ``pending`` (+ watch fields). Whole-
    corpus stages (ingest, assemble, describe) set ``run_all_fn`` instead and
    usually ``supports_item=False``. ``run_all_fn`` / ``start_watcher_fn`` /
    ``pending`` all receive/return via the shared :class:`Scheduler`.
    """

    name: str
    process_one: Callable[[Any], Any] | None = None
    parse_item: Callable[[str], Any] = Path
    pending: Callable[[], list] | None = None
    # watchdog (generic path):
    watch_dir: Path | None = None
    watch_match: Callable[[Path], bool] | None = None
    item_from_event: Callable[[Path], Any] | None = None
    # overrides for stages that don't fit the per-item mold:
    run_all_fn: Callable[[Scheduler, argparse.Namespace], None] | None = None
    start_watcher_fn: Callable[[Scheduler, argparse.Namespace], Any] | None = None
    # extra argparse args this stage wants, e.g. [("--dry-run", {"action": "store_true"})]
    extra_args: list[tuple[str, dict]] = field(default_factory=list)
    pool: str = "io"  # "cpu" | "io" — which shared pool run_all / watch submit to
    supports_all: bool = True
    supports_watch: bool = True
    supports_item: bool = True

    def pool_for(self, sched: Scheduler):
        return sched.cpu if self.pool == "cpu" else sched.io


def _run_all(stage: Stage, sched: Scheduler, args: argparse.Namespace) -> None:
    if stage.run_all_fn is not None:
        stage.run_all_fn(sched, args)
        return
    from concurrent.futures import as_completed

    items = list(stage.pending()) if stage.pending else []
    if not items:
        logger.info(f"[{stage.name}] nothing pending.")
        return
    logger.info(f"[{stage.name}] processing {len(items)} pending item(s)...")
    pool = stage.pool_for(sched)
    futures = {pool.submit(stage.process_one, it): it for it in items}
    done = 0
    for fut in as_completed(futures):
        it = futures[fut]
        try:
            fut.result()
            done += 1
            logger.info(f"[{stage.name}] [{done}/{len(items)}] done: {it}")
        except Exception as exc:
            logger.error(f"[{stage.name}] FAILED: {it} — {exc}")


def _start_watcher(stage: Stage, sched: Scheduler, args: argparse.Namespace):
    if stage.start_watcher_fn is not None:
        return stage.start_watcher_fn(sched, args)

    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    watch_dir = stage.watch_dir
    watch_dir.mkdir(parents=True, exist_ok=True)
    pool = stage.pool_for(sched)
    match = stage.watch_match or (lambda p: True)
    to_item = stage.item_from_event or (lambda p: p)

    def _safe(item):
        try:
            stage.process_one(item)
        except Exception as exc:
            logger.error(f"[{stage.name}] failed: {item} — {exc}")

    class _Handler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            p = Path(event.src_path)
            if match(p):
                logger.info(f"[{stage.name}] [watch] new: {p.name}")
                pool.submit(_safe, to_item(p))

    obs = Observer()
    obs.schedule(_Handler(), str(watch_dir), recursive=False)
    obs.start()
    return obs


def watch_stage(stage: Stage, sched: Scheduler):
    """Start a stage's watcher and return the observer. Used by the orchestrator
    to wire a stage into the cascade with the shared scheduler."""
    return _start_watcher(stage, sched, argparse.Namespace())


def run_stage(stage: Stage, argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog=f"python -m src.{stage.name}", description=f"{stage.name} stage"
    )
    if stage.supports_item:
        ap.add_argument("item", nargs="?", help="a single item to process")
    if stage.supports_all:
        ap.add_argument("--all", action="store_true", help="process all pending items")
    if stage.supports_watch:
        ap.add_argument("--watch", action="store_true", help="watch for new inputs and process as they land")
    ap.add_argument("--workers", type=int, default=None, help="pool size for this run (cpu + io)")
    ap.add_argument("--io-workers", type=int, default=None, help="override just the io pool size")
    for flag, kwargs in stage.extra_args:
        ap.add_argument(flag, **kwargs)
    args = ap.parse_args(argv)

    # First caller fixes pool sizes → this is how --workers overrides flow in.
    sched = get_scheduler(
        cpu_workers=args.workers,
        io_workers=args.io_workers if args.io_workers is not None else args.workers,
    )
    try:
        if getattr(args, "watch", False):
            logger.info(f"[{stage.name}] watching {stage.watch_dir} ...")
            obs = _start_watcher(stage, sched, args)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                obs.stop()
                obs.join()
                logger.info(f"[{stage.name}] stopped.")
        elif getattr(args, "all", False):
            _run_all(stage, sched, args)
        elif getattr(args, "item", None) is not None:
            stage.process_one(stage.parse_item(args.item))
        else:
            ap.print_help()
    finally:
        sched.shutdown(wait=True)
