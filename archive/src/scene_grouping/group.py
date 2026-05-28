"""
Scene grouping orchestrator.

Chains the content grouping and timeline generation stages via watchdog.
Called from main.py to wire both watchers into the shared executor.

For standalone usage, run the individual modules directly:
    python -m src.scene_grouping.llm_grouper --all
    python -m src.scene_grouping.llm_timeline --all
"""
from src.scene_grouping.llm_grouper import (
    start_watcher as start_group_watcher,
    stop_watcher as stop_group_watcher,
)
from src.scene_grouping.llm_timeline import (
    start_watcher as start_timeline_watcher,
    stop_watcher as stop_timeline_watcher,
)


def start_watcher(executor=None) -> tuple:
    """Start both watchers: sections → content_groups → timelines."""
    group_observer = start_group_watcher(executor=executor)
    timeline_observer = start_timeline_watcher(executor=executor)
    return (group_observer, timeline_observer)


def stop_watcher(observers: tuple) -> None:
    group_observer, timeline_observer = observers
    stop_group_watcher(group_observer)
    stop_timeline_watcher(timeline_observer)
