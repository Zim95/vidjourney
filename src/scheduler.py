"""
Shared bounded scheduler for the pipeline.

One process-wide instance owns the executor pools and a global subprocess
semaphore so that, however many stages run at once (standalone today, or under
the watchdog orchestrator later), the machine never oversubscribes:

- ``cpu`` — a ``ProcessPoolExecutor`` for genuinely in-process CPU work (PDF
  page parsing). Created lazily on first access so merely importing this module
  — or running a stage that only needs the semaphore — never spawns workers.
- ``io`` — a ``ThreadPoolExecutor`` for work that shells out to a child process
  (manim / ffmpeg / piper) or hits the network (Ollama, YouTube). Those threads
  spend their time blocked on the child or socket, so threads (not processes)
  are the right tool. Created lazily.
- ``subprocess_slot()`` — a context manager over a ``BoundedSemaphore`` that
  caps concurrent **child processes** across all ``io`` threads. This is the
  safety valve: manim/ffmpeg are CPU- and memory-heavy, so even a large ``io``
  pool must not launch more than a handful at once. Wrap every heavy
  ``subprocess.run`` in it.

Subprocess-spawning work must run on ``io`` (parent process), not ``cpu``: the
semaphore lives in the parent, so it can only bound children spawned by parent
threads — a ``cpu`` worker is a separate process and wouldn't share it. ``cpu``
is for pure in-process CPU (PDF parsing), which spawns nothing.

Pool sizes default to the ``[pipeline]`` config (``PIPELINE_PROCESS_WORKERS`` /
``PIPELINE_THREAD_WORKERS``), resolved at first call — never bound at import.
The first caller may override them (this is how a ``--workers`` flag will flow
in); later callers get the same instance.
"""
from __future__ import annotations

import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import contextmanager

from src.config.constants import PIPELINE_PROCESS_WORKERS, PIPELINE_THREAD_WORKERS


class Scheduler:
    """Owns the CPU/IO pools and the global subprocess semaphore."""

    def __init__(self, cpu_workers: int, io_workers: int, subprocess_limit: int):
        self.cpu_workers = max(1, cpu_workers)
        self.io_workers = max(1, io_workers)
        self.subprocess_limit = max(1, subprocess_limit)
        self._cpu: ProcessPoolExecutor | None = None
        self._io: ThreadPoolExecutor | None = None
        self._subproc_sem = threading.BoundedSemaphore(self.subprocess_limit)
        self._pool_lock = threading.Lock()

    @property
    def cpu(self) -> ProcessPoolExecutor:
        if self._cpu is None:
            with self._pool_lock:
                if self._cpu is None:
                    self._cpu = ProcessPoolExecutor(max_workers=self.cpu_workers)
        return self._cpu

    @property
    def io(self) -> ThreadPoolExecutor:
        if self._io is None:
            with self._pool_lock:
                if self._io is None:
                    self._io = ThreadPoolExecutor(max_workers=self.io_workers)
        return self._io

    @contextmanager
    def subprocess_slot(self):
        """Hold one of ``subprocess_limit`` slots for the duration of a child
        process. Blocks when all slots are taken."""
        self._subproc_sem.acquire()
        try:
            yield
        finally:
            self._subproc_sem.release()

    def shutdown(self, wait: bool = True) -> None:
        with self._pool_lock:
            if self._cpu is not None:
                self._cpu.shutdown(wait=wait)
                self._cpu = None
            if self._io is not None:
                self._io.shutdown(wait=wait)
                self._io = None


_scheduler: Scheduler | None = None
_scheduler_lock = threading.Lock()


def get_scheduler(
    cpu_workers: int | None = None,
    io_workers: int | None = None,
    subprocess_limit: int | None = None,
) -> Scheduler:
    """Return the process-wide :class:`Scheduler`, creating it on first call.

    The first caller fixes the sizes (defaults from the ``[pipeline]`` config);
    later callers get the same instance and their arguments are ignored.
    """
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                cpu = PIPELINE_PROCESS_WORKERS if cpu_workers is None else cpu_workers
                io = PIPELINE_THREAD_WORKERS if io_workers is None else io_workers
                sub = cpu if subprocess_limit is None else subprocess_limit
                _scheduler = Scheduler(cpu, io, sub)
    return _scheduler


@contextmanager
def subprocess_slot():
    """Module-level convenience: acquire a global subprocess slot from the
    shared scheduler. Wrap every CPU/memory-heavy ``subprocess.run`` (manim /
    ffmpeg / piper) so concurrent stages can't oversubscribe the machine."""
    with get_scheduler().subprocess_slot():
        yield
