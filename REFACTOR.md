# Refactor notes

What's worth cleaning, ordered by ROI. Not blocking — pipeline works. These
are the things I'd do *next* if I were touching this code with fresh eyes.

---

## High value

### 1. `llm_timeline.py` is doing too much (1053 lines)

It owns: routing logic, six different scene builders, alignment helpers,
batch-of-4 entity bookkeeping (still there even though concept cards
replaced entity stream as the default fallback), CLI plumbing.

Split into:
- `src/scene_grouping/timeline/__init__.py` — public `build_timeline` + `Scene` + `TimelineEvent`
- `src/scene_grouping/timeline/builders/heading.py`
- `src/scene_grouping/timeline/builders/paragraph_resource.py`
- `src/scene_grouping/timeline/builders/list_scene.py` (the shared list-scene engine)
- `src/scene_grouping/timeline/builders/paragraph_blank.py` (the 3-path router)
- `src/scene_grouping/timeline/serialize.py`
- `src/scene_grouping/timeline/cli.py`

Pure mechanical split — same module behavior, just smaller files.

### 2. Dead code from the entity-stream era

We replaced entity SPAWN streams with concept cards (commit `d9aa9fc`), but
left a lot of supporting infrastructure in case we needed to fall back:

- `_events_for_entity_result` in `llm_timeline.py` — only the quote path
  is actually used now (entity branch is dead)
- `llm_entity.py` — only `extract` is used, only for quote detection
- `_alignment_time_for_phrase` — was for entity SPAWN anchoring, unused
- `_find_word_position`, `_sentence_start_word_positions` — sentence-position
  fallback for entities, unused
- `ENTITY_SLOTS` + slot-allocator code in `dsl_compiler.py` — was for the
  2x2 grid layout, no SPAWN events emitted anymore
- `EntityAbstractShape`, `EntityActionShape`, `AutoRectangleShape` in
  `shape_objects.py` — entity pills, no longer instantiated
- `manim_constants.py` — `entity_abstract`, `entity_action` shape registrations

Two options for what to do with this:
(a) **Delete it** — cleanest. The git history preserves it if we ever want it back.
(b) **Keep but isolate** — move to `src/scene_grouping/_attic/` with a comment
    explaining why it's there.

I'd just delete. We have git.

### 3. Sequential bash batch script vs the pipeline's built-in parallelism

`/tmp/run_sections_4_20.sh` (the current section-4-20 batch) is fully
sequential. The pipeline already has:
- `process_all` in `llm_timeline.py` (uses `ThreadPoolExecutor`)
- `process_all_timelines` in `compile.py`
- `main.py` runs everything as a watchdog cascade with shared executors

The bash script ignores all of this. For the full 224-section run, we'd
save 30-50% wall time by either:
- Calling `main.py` (cascading watchers, all stages overlap) — the
  intended design
- Or using `--all` on each stage (still mostly sequential per stage, but
  within-stage parallelism kicks in)

The bash script was a stopgap. Worth replacing with a proper batch runner.

---

## Medium value

### 4. `dsl_compiler.py` event-type handlers (612 lines, one giant function)

`_compile_events` is a 350-line `for-elif` ladder. Each event type
(`SPAWN`, `ARROW`, `SHOW_RESOURCE`, `SHOW_HEADING`, etc.) has its own
30-50 line handler. Split into a dispatch dict:

```python
HANDLERS = {
    "SPAWN": _handle_spawn,
    "ARROW": _handle_arrow,
    "SHOW_RESOURCE": _handle_resource,
    ...
}
```

Each handler takes `(event, state)` and returns the emitted DSL lines.
State is the same dict the for-loop is mutating today.

### 5. `shape_objects.py` is a grab-bag (425 lines)

Eight shape classes in one file. They're mostly small but already three
of them share `_build_pill`. Split:
- `shape_objects/base.py` — `ShapeObject`, `_build_pill`
- `shape_objects/text.py` — `HeadingShape`, `QuoteShape`, `ListItemShape`, `ListTitleShape`, `ConceptCardShape`
- `shape_objects/geom.py` — `CircleShape`, `SquareShape`, `RectangleShape`, `AutoRectangleShape`
- `shape_objects/entity.py` — `EntityAbstractShape`, `EntityActionShape` (if kept)

### 6. SRT writer's "smoothing" is now coupled to the alignment path only

`_smooth_card_timings` is only called inside the alignment branch of
`generate_srt`. The parts.json and word-count branches don't get the same
min-dwell treatment. Either generalize the smoother to all branches or
document the asymmetry.

### 7. Configuration constants exported as flat module-level globals

`src/config/constants.py` exports 80+ symbols like `GROUPING_TIMELINES_DIR`,
`SUBTITLE_FONT_NAME`, etc. Works but cumbersome. Grouped namespaces would
read better:

```python
from src.config import grouping, subtitles, ollama
grouping.timelines_dir
subtitles.font_name
ollama.chat_model
```

Pure rename pass.

---

## Low value (nice-to-haves)

### 8. CLI duplication across stage modules

Every stage has its own argparse + `--watch` + `--all` boilerplate. Could
share a CLI helper:

```python
from src.utils.stage_cli import run_stage_cli
if __name__ == "__main__":
    run_stage_cli(process_section, process_all, start_watcher, ...)
```

Mostly cosmetic.

### 9. `ContentGroup` data model isn't a dataclass

It's currently a hand-written class with `from_text` / `to_text` methods
plus a bunch of properties. Could be a `dataclass` with explicit fields
and a separate serializer. Easier to type-check.

### 10. Pipeline directory paths are scattered

Every module imports its own subset of `GROUPING_*_DIR` constants. Could
have a single `PipelinePaths` namespace that's instantiated once and passed
through. Mostly affects testing — would make it easy to redirect a whole
pipeline run to a different root.

---

## What I'd actually do this week

1. **Delete the entity-stream dead code** (item 2). 30-min job, makes the
   codebase ~500 lines smaller, makes ARCHITECTURE.md accurate (right now
   it still hedges about entities).
2. **Split `llm_timeline.py`** (item 1). 2-hour job. Biggest readability
   win for new contributors and for me-next-month.
3. **Replace the bash batch script** (item 3). 1-hour job. Saves real wall
   time on full-PDF runs.

Items 4-10 are quality-of-life — defer until they actually bite.
