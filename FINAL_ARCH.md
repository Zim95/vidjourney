# Final Architecture — four-stage pipeline, maximally parallel

The target design for vidjourney: turn a PDF into scheduled YouTube videos in
four stages, every stage parallelized to the limit of the machine, dead code
removed, the LLM reduced to one optional touch, and a single human gate whose
only job is **correcting detector mistakes** (code blocks, tables, quotes) before
anything renders.

---

## 1. The four stages

```
1. INGEST → GROUPS   pages(∥) → detect → [USER GATE] → listify        (no LLM)
2. ASSETS            per section: narrate ─▶ render(raster)            (∥ across sections)
3. ASSEMBLE          sections = merge assets; parts = bin-pack + title + youtube details
4. PUBLISH           optional: "upload?" → link account → sequential drain
```

### Stage 1 — Ingest → groups
- Read the PDF, keep every element in global **reading order**.
- Detect elements (heading / paragraph / list_item / quote / code_block / table /
  image / …).
- **USER review gate** (the one human step): per section, inspect the detector's
  guesses and tune/relabel where wrong —
  - **code blocks**: line-by-line code⇄prose relabel, merge/split, promote/demote;
  - **tables**: tune table-detection parameters and re-detect;
  - **quotes**: confirm/adjust quote + attribution split.
  Saving a correction (a) rewrites the section/group artifact so render uses the
  fix, and (b) appends labelled data to `ingestion/ml/training_code_snippets/`
  so the next `train.py` improves the detector — the gate is the training-data
  flywheel.
- **Listify** (after detection is approved): keep existing list items; split
  embedded lists out of paragraphs into items; then turn the *remaining*
  paragraphs into list items too. Items found inside a paragraph become **nested**
  under that paragraph's lead item (`_split_embedded_list` already returns
  `(intro, items)` → intro = level-0, items = level-1).
- **No LLM.** Grouping is fully deterministic; the quote LLM is dropped (the gate
  handles quote edge-cases). The only LLM left in the whole pipeline is the
  optional `llm_title_generator` used in Stage 3.

### Stage 2 — Generate assets
- Per section, a **two-step chain** (NOT parallel siblings):
  `narrate (Piper) ─▶ render (raster)`.
- Narration gates render because the raster's camera-pan timing is computed from
  per-block narration durations (`block distance ÷ narration time`). The raster
  path internally renders the **canvas scene** (manim → tall PNG) then ffmpeg-pans
  it. `manim_scene` (the camera-animate alternative) is retired/flagged — one
  visual path only.
- The parallelism is **across sections**: section B narrates while section A
  renders.

### Stage 3 — Assemble
- **Sections**: merge each section's assets (silent video + narration → final
  `section_N.mp4`).
- **Parts**: greedy bin-pack sections into ≥10-min parts; name each via the
  optional `llm_title_generator`; ffmpeg-concat into `parts/<title>.mp4`.
- **YouTube details live here**, not in Stage 2 — title/description/tags are
  *per-part* and can't be generated until parts are defined. Describe is the
  producer; Publish is the consumer.

### Stage 4 — Publish (optional)
- Gated behind "Do you want to upload?" → link Google account (OAuth).
- **Strictly sequential** drain over parts: human-like pacing, scheduled
  `publishAt`, idempotent ledger. The one stage that must never be parallel.

---

## 2. Guiding principles

- **Pipeline per *item*, not per *stage*.** A section that's been grouped starts
  rendering while siblings are still being grouped. No stage-wide barriers except
  the cheap `detect_sections` one.
- **One global bounded scheduler.** Target is a resource-constrained Mac, so
  "as parallel as possible" = *saturate cores, never oversubscribe*:
  **CPU pool** (≈cores−1) for page-parse/manim/ffmpeg/Piper; **I/O pool** (large)
  for Ollama/YouTube; a **global subprocess semaphore** caps total live
  subprocesses.
- **Idempotent disk cache = free parallelism + safe reruns.** Every stage skips
  when output is newer than input. Preserve it.
- **Batch the network, never loop it.** Embed all code lines in one async pass,
  not one HTTP call per line.
- **One human gate**, and it must **auto-pass clean sections** (see §4).

---

## 3. Dead code to cut first

- **Drop the quote LLM entirely** — `classify_paragraph` (only `quote` consumed)
  and `extract_quote` (a deterministic em-dash split). The review gate covers the
  edge cases.
- **Delete** `narrator.generate_narration()` + `parse_voiceover()` (archive-only)
  and `ffmpeg_merge.concat_videos()` (superseded by `build_video._concat_videos`).
- **One render path** — keep `build_raster.py`; retire `build.py`'s animate path
  and `manim_scene.ScrollScene`.
- **Rename** `scene_grouping/llm_grouper.py` → `grouping/grouper.py` (no LLM left).
- **Fix stale docs** (README/ARCHITECTURE/REFACTOR reference a deleted `main.py`
  and the deleted timeline/DSL pipeline).
- **Drop** `scroll_optimized/` + `content_groups_optimized/` validation harness.

---

## 4. Concurrency implementation

Three mechanisms, each where it fits: **watchdogs cascade *across* stages**
(every boundary is a file the next stage watches), **multiprocessing / threading
parallelize *within* a stage**, and **asyncio** batches network I/O. One human
gate breaks the auto-cascade.

### Mechanism per stage

| Stage | Within-stage parallelism | Cross-stage trigger | Why |
|---|---|---|---|
| 1a. Ingest pages | `ProcessPoolExecutor` (≈cores) | — (in-memory) | CPU-bound PyMuPDF; needs processes |
| 1b. Cleanup + embeddings | per-section futures + **one batched async** embed pass | writes `sections/*.txt` | regex cheap; batch Ollama (asyncio) |
| 1c. Group + listify | `ThreadPoolExecutor` | **watchdog** `sections/` → `groups/content_groups/*.txt` | light regex; threads overlap I/O |
| 1d. Review GATE | *human, async* | writes `groups/approved/*.txt` | the one non-automatable step |
| 2. Assets | per-section pipeline `narrate→render`, fanned across sections | **watchdog** `groups/approved/` → `output/section_*.mp4` | manim/ffmpeg/piper subprocesses → ThreadPool drives N concurrent |
| 3. Assemble | per-part (fires when its sections exist) | **watchdog** `output/` → `parts/*.mp4` + `descriptions/*.md` | ffmpeg concat (CPU) + optional title (I/O) |
| 4. Publish | **none — serial** | *manual trigger, not watched* | pacing + `publishAt` + ledger |

### Watchdog cascade (file boundaries)

```
ingest ──▶ sections/*.txt
              │  watchdog: GROUPER (ThreadPool)  → group + listify
              ▼
        groups/content_groups/*.txt
              │  ◀── HUMAN GATE (review code/tables/quotes, re-detect, approve)
              ▼
        groups/approved/*.txt
              │  watchdog: ASSETS (per-section narrate→render, CPU pool)
              ▼
        output/section_*.mp4
              │  watchdog: ASSEMBLE (per-part, CPU + I/O pools)
              ▼
        parts/*.mp4 + descriptions/*.md
              │  ◀── MANUAL: "Upload?" → OAuth → sequential drain
              ▼
           YouTube
```

Three live `watchdog.Observer`s (grouper, assets, assemble) + one in-process
process pool (pages) + one manual stage (publish). This is what the deleted
`main.py` aimed at — now with the human gate and shared pools.

### Two rules that make or break it

**(a) The gate must auto-pass clean sections.** For a 224-section book, a human
clicking through every section re-serializes the whole pipeline. So the review
watcher only **queues sections with a low-confidence code block / table /
ambiguous quote**; everything else writes straight to `approved/`. The human then
touches ~20–40 sections, not 224 — and reviews section 3 *while* sections 50–60
are still ingesting and section 1 is already rendering. The human overlaps the
machine instead of blocking it.

**(b) One shared scheduler, or the Mac thrashes.** Three watchdogs each spawning
their own executor = ingest-manim + assets-manim + assemble-ffmpeg fighting for
cores. Instead, every handler submits to shared pools behind a global semaphore:

```
   GROUPER ┐
   ASSETS  ├──submit──▶ [ CPU pool ≈cores-1 ]  page/cleanup/narrate/manim/ffmpeg
   ASSEMBLE┘            [ I/O pool (large)    ]  embeddings/llm-title/youtube
                        [ global subprocess semaphore ] ← Mac safety valve
```

Wire `PIPELINE_PROCESS_WORKERS` → CPU pool, `PIPELINE_THREAD_WORKERS` → I/O pool
(today defined but only `grouper` uses them).

### Why output stays identical to a serial run ("same results as DDIA")

Parallelism changes throughput, not artifacts, because the work is **independent +
deterministic + idempotent**:
- **Independent** — section 7's render can't affect section 8's; execution order
  is irrelevant.
- **Deterministic** — global reading order, grouping, listification, and
  bin-packing produce the same bytes regardless of concurrency. The only
  non-deterministic piece, the LLM part title, becomes **optional + cached**.
- **Idempotent** — newer-than-input skipping means crashes/reruns resume; one
  file per section/part means parallel writers never collide.

So a 224-section book yields the **same 81 parts** — bit-for-bit on the
deterministic stages — but the previously-serial render stage now scales close to
linearly with cores until manim/ffmpeg saturate the CPU. Publish is unchanged
(its slowness is the intended pacing). The only behavioral addition vs the
original run is the human gate, whose wall-clock cost is just the handful of
sections you actually inspect.

### Running stages standalone — one core, two triggers

Every stage is **the same code with two entry points** — the `--all` / `--watch`
convention the modules use today (`grouper` already has `start_watcher` +
`process_all` + single-item), generalized to all stages. Each stage defines:

- **`process_one(item)`** — the pure, idempotent unit of work;
- **`pending()`** — items whose output is missing/stale;
- **`start_watcher(pool)`** — a `watchdog.Observer` that submits `process_one`
  to the shared pool on file-create (event-driven cascade);
- **`run_all(pool)`** — submit every `pending()` item to the shared pool and gather (batch).

A shared `src/utils/stage_cli.py` wires the argparse so each stage's `__main__` is
three lines. Every stage gets the same contract:

```
python -m src.<stage> <item>        # one item (debug)
python -m src.<stage> --all         # all pending, fanned across --workers
python -m src.<stage> --watch       # watchdog: fire as files land
python -m src.<stage> --workers N   # pool size (default: config)
```

**The only difference between the two triggers is *when* `process_one` runs** —
the watcher calls it per file-create as the previous stage trickles output;
`--all` calls it for every pending item at once. **Both submit to a pool of the
same size**, so running a stage standalone after the previous one finishes yields
the same parallelism (and same artifacts) as the cascade. Chain by hand:

```
python -m src.ingestion --all && \
python -m src.grouping  --all && \
python -m src.assets    --all && \
python -m src.assembler --all
```

…or run `src.pipeline` to cascade all of them via watchdogs. Same per-stage code
either way — only the trigger differs.

The human gate is the one stage that isn't fully automatable, but it still has a
terminal mode: `python -m src.grouping --approve-clean` auto-passes
high-confidence sections and queues only flagged ones for the UI.

---

## 5. What the LLM does — reduced to one optional touch

Two model roles: the **chat LLM** (Ollama gemma/llama) and the **embedding model**
(`nomic-embed-text` → the code RandomForest).

**Chat LLM** — was 3 uses, now **1 optional**:

| Use | Where | Verdict |
|-----|-------|---------|
| Quote split | `extract_quote` | **Remove** — deterministic em-dash split (`split_quote_and_prose`). |
| Quote detect | `classify_paragraph` | **Remove** — extend the `is_quote()` regex; the gate handles the rest. |
| Part title | `_llm_part_title` → `llm_title_generator` | **Keep, optional** — the only generative use; has a deterministic fallback; cache results. |

→ The pipeline can run with **zero chat-LLM dependency** (only slightly less
polished part titles).

**Embedding model** — the only essential model, and the source of the
mislabeled-code problem. We don't remove it; we make it **correctable** (the
review gate) and **fast** (batch the embeddings — §4 stage 1b).

---

## 6. Full DAG

```
                              PDF
                               │
        ┌──────────────────────┴───────────────────────┐
        │  STAGE 1a: page read           [CPU ProcessPool ≈ cores]
        │  chunk1  chunk2  chunk3  chunk4  ... (parallel)          │
        └──────────────────────┬───────────────────────┘
                               ▼
                 detect_sections ── serial barrier (cheap, ms) ──┐
                               │                                  │
        1b cleanup (∥ per section) + BATCHED embeddings ─────────┘ (I/O pool)
                               │  writes sections/*.txt
                               ▼
        1c GROUP + LISTIFY  (watchdog, ThreadPool)
                               │  writes groups/content_groups/*.txt
                               ▼
        1d  ┤ HUMAN GATE ├  review code/tables/quotes; auto-pass clean
                               │  writes groups/approved/*.txt
   ╔═══════════════════════════╪══════════════════════════════════════╗
   ║  STAGE 2 ASSETS — per-section pipeline, ∥ across sections (CPU)    ║
   ║  s1: narrate ─▶ render ─▶ output/section_1.mp4                     ║
   ║  s2:    narrate ─▶ render ─▶ output/section_2.mp4                  ║
   ║  s3:       narrate ─▶ render ─▶ ...                                ║
   ╚═══════════════════════════╪══════════════════════════════════════╝
                               │  (watchdog on output/)
                               ▼
        STAGE 3 ASSEMBLE  (∥ per part; a part fires when its sections exist)
          part1  part2  part3 ...   →  parts/*.mp4
          + llm_title_generator (optional, I/O)  + youtube details → descriptions/*.md
                               ▼
        ╔══════════════════════════════════════╗
        ║ STAGE 4 PUBLISH — optional, SEQUENTIAL║
        ║  "Upload?" → OAuth →                  ║
        ║  part1 ▸ (delay) ▸ part2 ▸ (delay) ▸… ║  scheduled publishAt + ledger
        ╚══════════════════════════════════════╝

POOLS:  [CPU ≈ cores-1]  page-parse · cleanup · narrate · manim · ffmpeg
        [I/O large]       Ollama embeddings (batched) · optional title · YouTube
        [global semaphore] caps total live subprocesses (Mac-safe)
```

---

## 7. Proposed package layout

```
src/
├── pipeline.py          # orchestrator: builds the cascade, owns the gate
├── scheduler.py         # CPU pool + I/O pool + global subprocess semaphore
├── utils/
│   └── stage_cli.py     # shared --all/--watch/--workers entry-point wiring
├── config/
├── ingestion/           # pages (ProcessPool) + per-section cleanup
│   └── ml/              # code RF + BATCHED embeddings + training data
├── grouping/            # grouper.py (no LLM) + listify + quotes (deterministic)
├── render/              # ONE path (raster); narrate as a sub-stage
├── assembler/           # parts (∥) + optional llm_title_generator + descriptions
├── publisher/           # YouTube (sequential) + describe producer
└── ui/                  # FastAPI app + review/labelling gate
```

---

## 8. UI flow (drives the orchestrator + hosts the gate)

Stack: **FastAPI** (triggers stages, streams progress via SSE/websocket, reuses
existing module functions) + **htmx/HTML** (the per-line code labeller needs real
interaction).

```
1. PROJECT     pick/upload PDF, set book title + part length → Ingest
2. INGEST      live parallel page-read; sections appear
3. REVIEW      per section: groups rendered; ⚠ low-confidence code/tables/quotes
   (GATE)        └─ LABELLER: line-by-line code⇄prose, merge/split, table re-tune,
                    quote split → Save (fix artifact + append training data) → Approve
4. RENDER      per-section lanes (parallel); preview section_N.mp4 as they finish
5. ASSEMBLE    parts auto-appear; descriptions editable (title/desc/tags/chapters)
6. PUBLISH     set start/interval; preview publishAt; confirm channel; sequential upload
```

The gate is the only blocking step; clean sections auto-pass so the human
overlaps the machine.

---

## 9. Migration order (by ROI)

1. **Render fan-out (Stage 2)** — largest wall-clock win; sections already independent.
2. **Batch embeddings (Stage 1b)** — kills the per-line HTTP pathology; isolated.
3. **Cut dead code (§3)** — fast, de-risks everything after.
4. **Orchestrator + scheduler + watchdog cascade (§4)** — wires the per-section pipeline.
5. **UI + review/labelling gate (§8)** — the human-in-the-loop surface.
6. **LLM removal (§5)** — drop the quote LLM; keep only the optional title.
```
