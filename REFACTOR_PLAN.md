# Refactor: vidjourney → terminal-only, orchestrated pipeline

> Execution plan for the [FINAL_ARCH.md](FINAL_ARCH.md) target, re-scoped **terminal-only
> (no UI)**. Phases are green-lit and shipped one at a time. Dead-code cut is first.

## Context

vidjourney turns a PDF into narrated explainer videos and uploads them to YouTube.
Today it is a set of **standalone stages run by hand** — no orchestrator, no shared
scheduler, no human review gate, and several dead/duplicate code paths. The DDIA
upload (all 81 parts) is now complete, so this is the moment to pay down that debt.

The target is a clean four-stage pipeline that is **maximally parallel, idempotent, and
dual-triggerable** (every stage runnable manually *or* via a watchdog cascade), with a
**single human gate** that corrects detector mistakes (code/tables/quotes). One firm
change vs the doc: **no UI** — the gate and the whole app are **terminal-only**, and the
gate is *interactive* (it asks the user questions in the terminal, auto-passing clean
sections so a 224-section book only prompts on the flagged handful).

### Ground truth (verified in code — the doc is stale in places)
- **No orchestrator/scheduler/gate/`approved/` dir exists.** All aspirational.
- **The quote chat-LLM is LIVE, not dead** — `classify_paragraph`/`extract_quote`
  fire per-group on every render via `build.py._groups_to_blocks` → `build_raster`.
  Removing it is a *behavioral change* (Phase 3), not a dead-code cut.
- **`build.py` is mostly live shared code** — `build_raster` depends on it for
  `_groups_to_blocks`, `_layout`, `_narrate_blocks`, etc. Only its manim-animate tail
  (`_render_manim` + `__main__`) is dead.
- **`ScrollScene` can't be deleted** — `canvas_scene.CanvasScene` subclasses it.
- **`is_quote` + `split_quote_and_prose` already exist** in
  [quote_attribution.py:47,52](src/ingestion/quote_attribution.py#L47) — extend them,
  don't rewrite.
- **Config is flat import-time constants** ([constants.py](src/config/constants.py));
  it also carries ~60 dead DSL/canvas/icons constants that `configuration.cfg` already
  stripped (they sit on fallbacks).
- **Embeddings inference is per-line over HTTP** — the pathology
  ([code_cleanup.py:248-257](src/ingestion/section_detection/code_cleanup.py#L248)).
- Only the grouper has the `--all/--watch` shape today; only it reads
  `PIPELINE_THREAD_WORKERS`. `PIPELINE_PROCESS_WORKERS` is defined but unused.

---

## Plan — 8 phases, executed on green-light per phase

Phase 1 (dead code) is first per directive. Each later phase is independently shippable
and verifiable. Dependencies: gate (P6) needs batched proba (P2) + extended `is_quote`
(P3) + stage contract (P5); orchestrator (P7) needs scheduler (P4) + gate.

| # | Phase | Risk | Why here |
|---|---|---|---|
| 1 | Dead-code cut (confirmed-dead only) | low | first priority; de-risks everything |
| 2 | Batch embeddings inference | low | isolated; unblocks the gate's re-detect |
| 3 | Remove quote chat-LLM | med | it's *live*; leaves ollama only in optional title-gen |
| 4 | Shared scheduler | med | CPU/IO pools + subprocess semaphore |
| 5 | `stage_cli` dual-trigger contract | med | every stage manual *and* `--watch` |
| 6 | Interactive terminal gate | high | the hard one — flags sidecar, prompts, flywheel |
| 7 | Orchestrator (`src/pipeline.py`) | med | watchdog cascade over real dirs |
| 8 | Package renames | low | pure churn, deliberately last |

### Phase 1 — Dead-code cut  *(lowest risk; do first)*
Delete only the **confirmed-dead**; leave the live quote-LLM for Phase 3.
- `src/narration/narrator.py` — delete `generate_narration()` + `parse_voiceover()`
  (keep `narrate_text`).
- `src/assembler/ffmpeg_merge.py` — delete `concat_videos()` + `merge_audio_video()`
  (keep `concat_wavs`, still live via `build.py`).
- `src/scroll/build.py` — delete the manim-animate tail: `_render_manim()` (~:746) and
  the `__main__` (~:819). **Keep everything else** (`build_raster` depends on it).
- `scripts/render_optimized.py` — delete (unused validation harness; also removes the
  only monkey-patcher of flat constants, which de-risks Phase 4). Remove the
  `pipeline/scroll_optimized/`, `pipeline/groups/content_groups_optimized/`,
  `pipeline/scroll/raster_optimized/` output dirs.
- [constants.py](src/config/constants.py) — delete the ~60 dead constants: `[scenes]`
  (`SCENES_DIR`, `DSL_*`), `[render]` (`RENDER_DIR`, `SCENE_TO_RENDER_MAX_WORKERS`),
  `MANIM_SCENE_FILE/CLASS/PREVIEW`, `RENDER_TO_MANIM_MAX_WORKERS`, the canvas/
  concept-card/list-layout/timing/aligner/`ICONS_*`/`TIMELINES` blocks. Keep anything
  build_raster/build/build_video/grouper actually import (grep each before deleting).
- **Verify:** `python -m src.scroll.build_raster 1` renders an identical mp4;
  `python -m src.assembler.build_video --dry-run` unchanged; `git grep` of each deleted
  symbol returns nothing.

### Phase 2 — Batch embeddings inference  *(isolated; unblocks the gate)*
Kill the one-HTTP-POST-per-line pathology.
- [train.py](src/ingestion/ml/train.py) — add
  `predict_is_code_proba_batch(texts) -> list[float]`: load model once, hand-crafted
  features per text, **one** `get_embeddings(texts)` call, `predict_proba[:,1]`, force
  failed-embedding rows to `1.0` (conservative, matches current fallback). Preserve
  positional 1:1 mapping — do **not** drop `None` rows. Reimplement single
  `predict_is_code_proba` as `..._batch([t])[0]`.
- [code_cleanup.py:214-284](src/ingestion/section_detection/code_cleanup.py#L214) —
  restructure `split_code_blocks_by_ml` into **collect → embed-once → classify**: walk
  all code blocks collecting non-empty lines into one flat list, embed in a single
  batch call, then replay the existing threshold/grouping logic reading precomputed
  probas. Byte-identical output (same probas, same threshold).
- **Verify:** `python -m src.ingestion.ml.line_proba` probas unchanged; time ingest on
  a code-heavy section before/after (expect large drop).

### Phase 3 — Remove the quote chat-LLM  *(behavioral)*
Leaves ollama chat used **only** by the optional `build_video._llm_part_title`.
- [quote_attribution.py](src/ingestion/quote_attribution.py) — add
  `split_quote_body_and_attribution(text) -> (body, attribution)` (em/en-dash + author
  tail, incl. bare-name + non-Gregorian/range years); extend `is_quote` so
  low-confidence bare-name attributions are *detectable* but routed to the gate as
  "ambiguous", not auto-tagged.
- [build.py](src/scroll/build.py) — delete imports (:50-51); replace `extract_quote`
  (:451) and the `classify_paragraph` safety-net branch (:475-485) with the
  deterministic helpers.
- Delete `src/scene_grouping/llm_classifier.py` + `llm_quotes.py` **after**
  `git grep 'classify_paragraph\|extract_quote' src` is clean.
- **Verify:** render a section with a year-bearing quote and a bare-name quote with
  `OLLAMA_BASE_URL` pointed at a dead port — build succeeds, no `/api/chat` on the
  render path.

### Phase 4 — Shared scheduler
- New `src/scheduler.py`: `Scheduler(cpu_workers, io_workers, subprocess_limit)` with a
  `ProcessPoolExecutor` (CPU: page-parse/manim/ffmpeg), `ThreadPoolExecutor` (IO:
  grouping/embeddings/llm-title), a `BoundedSemaphore` subprocess valve, and a
  `subprocess_slot()` contextmanager. Lazy singleton `get_scheduler(...)` resolves
  defaults from `PIPELINE_PROCESS_WORKERS`/`PIPELINE_THREAD_WORKERS` **at call time**
  (never rebinds constants — this is how `--workers` overrides cleanly).
- Wrap the ~8 `subprocess.run` sites (build.py, build_raster.py, ffmpeg_merge.py,
  build_video.py) in `sched.subprocess_slot()`. Replace ingest's own
  `ProcessPoolExecutor` ([ingest_pdf.py:108](src/ingestion/ingest_pdf.py#L108)) and the
  grouper's own `ThreadPoolExecutor` with submits to the shared scheduler.
- **Verify:** render one section → identical mp4; total live subprocesses never exceed
  the limit.

### Phase 5 — `stage_cli` + per-stage dual-trigger contract
- New `src/utils/stage_cli.py`: a `Stage` protocol (`process_one`, `pending`,
  `run_all`, `start_watcher`, `parse_item`) + `run_stage(stage)` owning shared argparse:
  `<item>` / `--all` / `--watch` / `--workers N`. Each stage `__main__` becomes 3 lines.
- Retrofit stages: **grouping** (already conformant — least change), **render**,
  **assemble**, **describe**. **Ingest** adopts a degenerate single-item form
  (PDF-in; `--all/--watch` disabled). **Upload** opts out by design (keeps its bespoke
  sequential argparse). `run_all` and `start_watcher` both submit to the same
  `Scheduler`, guaranteeing standalone == cascade parallelism/artifacts.
- **Verify:** each stage runnable as `<item>`, `--all`, `--workers 2`, `--watch`.

### Phase 6 — Interactive terminal review gate
- **Flags sidecar at ingest**: `pipeline/sections/flags/section_N.json` written by
  `SectionWriter`, populated from (a) code lines whose proba is within
  `ML_CODE_LINE_CONFIDENCE_MARGIN` of `ML_CODE_LINE_THRESHOLD` (side-channel out of the
  Phase-2 batch), (b) table regions with score near `INGESTION_TABLE_SCORE_THRESHOLD`
  (`TableDetectionUtils.evaluate` already returns the score), (c) ambiguous em-dash
  quotes (extended `is_quote` fires but high-confidence doesn't). No flags → no sidecar
  → auto-pass.
- New `src/scene_grouping/review_gate.py` (mirrors grouper structure, joins the
  stage_cli contract): reads `content_groups/`, writes new `groups/approved/`.
  `--approve-clean`/`--all`/`--watch` = batch auto-pass (never `input()` in pooled/
  watched runs); single-section or `--review` = interactive prompts (accept / relabel
  code line ranges / keep-or-demote table / confirm quote split). Code relabels edit
  the `resources/code_blocks/*.txt` source and re-group that one section (code is lossy
  once it becomes an IMAGE ref — *fiddliest part*).
- **Flywheel**: code relabels append `{text,label}` JSON to
  `src/ingestion/ml/training_code_snippets/` in the schema `build_code_training_data`
  already globs, so the next `train.py` improves the detector.
- **Repoint render** to read `GROUPING_APPROVED_DIR` (build.py:776, build_raster.py:52)
  with fallback to `content_groups/` if approved missing.
- New constants: `GROUPING_APPROVED_DIR`, `INGESTION_FLAGS_DIR`,
  `ML_CODE_LINE_CONFIDENCE_MARGIN`, `INGESTION_TABLE_SCORE_MARGIN`.

#### Sample gate transcript
```
── Review: section_27  (3 flags) ──────────────────────────────
[1/3] CODE BLOCK  resources/code_blocks/27_66_code_blocks_1.txt
  2 lines are borderline (threshold 0.40):
    L4  proba=0.48  |  return the median of the sorted list
    L7  proba=0.37  |  db.exec("SELECT * FROM users")
  (a)ccept  (r)elabel  (v)iew  (s)kip  > r
    Line 4 → (c)ode/(t)ext? > t     Line 7 → (c)ode/(t)ext? > c
  ✓ re-split applied · 2 labels saved
[2/3] TABLE  page 66  score=2.8 (threshold 3.0)   (k)eep/(d)emote/(v)iew  > k
[3/3] QUOTE?  "…invent it. —Alan Kay"  → body/attribution split  (q)/(p)/(e)  > q
Section 27 approved → pipeline/groups/approved/section_27.txt
```
- **Verify:** clean section auto-passes silently; borderline section prompts, applies
  relabel to `approved/`, and drops a labelled JSON; `train.py` dataset count rises.

### Phase 7 — Orchestrator (`src/pipeline.py`)
- Wires ingest (submits page chunks to `sched.cpu`) + 4 watchers on real dirs:
  grouping (`sections/`→`content_groups/`), gate (`content_groups/`→`approved/`),
  render (`approved/`→`output/`), assemble+describe (`output/`→`parts/`+
  `descriptions/`). One shared `Scheduler` injected into every `start_watcher`.
  `--no-gate` copies `content_groups/`→`approved/` for dev runs. Publish stays manual.
- Add `watchdog` to `pyproject.toml` deps (today a lazy import in one module).
- **Verify:** `python -m src.pipeline sample.pdf --no-gate` produces parts +
  descriptions end-to-end on a 2–3 section PDF; gated run pauses flagged sections.

### Phase 8 — Package renames  *(deferred; pure mechanical churn)*
`scene_grouping→grouping`, `scroll→render`, `scripts/generate_descriptions.py→
src/publisher/describe.py`. Do **last**, as an isolated git-mv + import-rewrite commit,
so it doesn't make the orchestration diffs unreviewable. **Verify:** full re-run;
`git grep 'scene_grouping\|src\.scroll'` empty.

---

## Key risks
- **watchdog on macOS** can fire `on_created` before writes finish. Mitigate with the
  existing idempotent skip-if-output-exists + a "file size stable" poll in render/
  assemble handlers (mp4 partial reads are fatal).
- **ProcessPool picklability** — anything submitted to `sched.cpu` must be top-level
  (build_section_raster, ingest page fn qualify). The deleted `render_optimized`
  monkey-patch would not have survived process boundaries — another reason it goes in P1.
- **Gate re-point is behavioral** — render must read `approved/`; the `--no-gate`/
  fallback copy path must exist *with* Phase 6 or render starves.
- **Ingest double-pooling** — must submit to `sched.cpu`, not spawn its own pool, or it
  oversubscribes the exact way the scheduler exists to prevent.

## Deferred / explicitly out of scope
- No config-object rewrite (flat constants stay; `--workers` solved by explicit pool
  sizing). No UI/FastAPI. No changes to the working upload stage. Renames last.