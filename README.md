# VidJourney

Turn a PDF into a folder of narrated explainer videos, bundled into ~10-minute
"Part" videos and optionally published to YouTube on a schedule.

VidJourney ingests a PDF, extracts text + figures + code blocks + tables, groups
them per section, narrates everything with Piper TTS, renders each section as a
vertically-scrolling canvas with Manim + ffmpeg (camera panning in sync with the
narration), packs consecutive sections into ≥10-minute Parts, and can upload
those Parts to YouTube — scheduled, paced, and added to a playlist.

It's a **terminal-only** application. Every stage runs standalone *or* as part of
a watchdog cascade driven by a single orchestrator, and one optional human
**review gate** lets you correct detector mistakes before anything renders. The
design is documented in **[FINAL_ARCH.md](FINAL_ARCH.md)**; the refactor that got
here is in **[REFACTOR_PLAN.md](REFACTOR_PLAN.md)**.

---

## Pipeline at a glance

```
PDF
 │
 ▼
[1] INGEST    PDF → pipeline/sections/section_*.txt         (text, figures, code, tables)
[2] GROUP     sections → pipeline/groups/content_groups/    (deterministic grouping + listify)
[3] GATE      content_groups → pipeline/groups/approved/    (auto-pass; human reviews the flagged few)
[4] RENDER    per section → narrate (Piper) → scroll canvas (Manim) → ffmpeg pan
 │            approved → pipeline/scroll/output/section_N_raster.mp4
[5] ASSEMBLE  pack consecutive sections into ≥10-min Parts → pipeline/scroll/parts/
 │            named "<Book> - Part <n> - <Summary>.mp4"
[6] DESCRIBE  Parts → pipeline/descriptions/part_NN.md      (paste-ready YouTube metadata)
[7] PUBLISH   (optional, manual) upload Parts to YouTube — scheduled, paced, into a playlist
```

Everything is **idempotent**: each stage skips work whose output is newer than
its input, so reruns and crashes resume cleanly.

---

## Prerequisites

- Python 3.11+ with [uv](https://docs.astral.sh/uv/)
- [ffmpeg](https://ffmpeg.org/) (+ `ffprobe`)
- [Ollama](https://ollama.com) with **`nomic-embed-text`** pulled (embeddings — required
  for code detection and the review gate). A chat model (`[ollama] chat_model`) is
  **optional**: it's used only by the Part-title generator, which has a deterministic
  fallback, so the whole pipeline runs with zero chat-LLM dependency.
- [Piper TTS](https://github.com/rhasspy/piper) voice at
  `~/.local/share/piper-voices/en_US-lessac-medium.onnx`
- `models/code_rf.joblib` — Random Forest for code-vs-prose classification
  (see *ML model training* below; falls back to heuristics if missing)
- For YouTube upload only: `uv add google-api-python-client google-auth-oauthlib
  google-auth-httplib2` and an OAuth client (see *Publishing to YouTube*)

`./install.sh` handles the core dependencies (except the ML model and the Google
libraries).

---

## Quick start — the orchestrator

Run the whole cascade from one command. It ingests the PDF, then each stage fires
as its input files land, all sharing one bounded scheduler:

```bash
python -m src.pipeline /path/to/book.pdf              # full cascade (ingest → … → describe)
python -m src.pipeline /path/to/book.pdf --no-gate    # skip the review-gate hop
python -m src.pipeline /path/to/book.pdf --workers 6  # cap the pool size for this run
```

The cascade runs `ingest → group → gate (auto-pass) → render → assemble → describe`.
Publishing stays manual (see below). Interactive review is an out-of-band step you
run on whichever sections you want to inspect — the cascade's gate hop only
auto-passes, so it never blocks.

---

## Running stages individually

Every stage is also a standalone command with the **same contract** — handy for
partial reprocessing (e.g. re-render one section after a change):

```
python -m src.<stage> <item>       # process one item (debug)
python -m src.<stage> --all        # process all pending items, fanned across the pool
python -m src.<stage> --watch       # watchdog: process inputs as they land (cascade)
python -m src.<stage> --workers N   # pool size for this run
```

| Stage | Module | Notes |
|---|---|---|
| Ingest | `src.ingestion.ingest_pdf <book.pdf>` | single PDF in (no `--all/--watch`) |
| Group | `src.grouping.llm_grouper` | `<section.txt>` / `--all` / `--watch` |
| Gate | `src.grouping.review_gate` | see *Review gate* below |
| Render | `src.render.build_raster` | `<N>` (section number) / `--all` / `--watch` |
| Assemble | `src.assembler.build_video` | `--all` (build Parts); `--all --dry-run` to preview |
| Describe | `src.publisher.describe` | `--all` (write all `part_NN.md`) |
| Publish | `src.publisher.upload` | intentionally sequential + manual (see below) |

```bash
# examples
python -m src.ingestion.ingest_pdf /path/to/book.pdf
python -m src.grouping.llm_grouper --all
python -m src.grouping.review_gate --approve-clean      # auto-pass everything, no review
python -m src.render.build_raster --all                 # render all pending sections
python -m src.assembler.build_video --all
python -m src.publisher.describe --all
```

Render reads the gate's `approved/` output, falling back to `content_groups/` for
any section that hasn't been through the gate — so an ungated run still renders.

---

## Review gate

The gate sits between grouping and render. It **auto-passes clean sections** and
only asks you about the handful with borderline detections, so a 200-section book
needs a few prompts, not hundreds.

```bash
python -m src.grouping.review_gate --approve-clean   # batch: auto-pass all pending (no questions)
python -m src.grouping.review_gate --watch           # cascade: auto-pass sections as they land
python -m src.grouping.review_gate <section.txt>     # interactively review ONE section
python -m src.grouping.review_gate --review          # interactively review all pending sections
```

Interactive review covers:

- **Ambiguous quotes** — a paragraph ending in a bare-name / non-year em-dash
  attribution. Confirming shows it as a quote; declining keeps it prose. Render
  trusts these decisions.
- **Borderline code lines** — code whose classifier probability sits near the
  threshold. Your correction is appended to
  `src/ingestion/ml/training_code_snippets/` (the **training-data flywheel**), so
  the next `train.py` + re-ingest improves the detector. (The current section's
  rendered code image updates on the next ingest, not live.)

Batch modes never prompt and never block, so the automated cascade always drains.

---

## Publishing to YouTube

The uploader reuses the `part_NN.md` description files and uploads each Part as a
**private, scheduled** video (it goes public on its `publishAt` date), adds it to
a playlist, sets a thumbnail, and records each upload in a ledger so reruns never
double-upload.

One-time setup:

1. Google Cloud Console → enable **YouTube Data API v3** → create an OAuth client
   of type **Desktop app** → download the JSON. (No billing/card required — the
   API runs on a free daily quota.)
2. Point `[youtube] client_secrets_file` at that JSON. The token is written to
   `token_file` after the first browser sign-in and reused after.
3. Set `[youtube]` `playlist_id`, `publish_start_date`, and the thumbnail.

```bash
python -m src.publisher.upload --whoami       # confirm which channel
python -m src.publisher.upload --list         # list discovered Parts + schedule
python -m src.publisher.upload --dry-run      # full plan, no upload
python -m src.publisher.upload --limit 6      # upload N pending (≈6/day on free quota)
python -m src.publisher.upload --part 14,15   # upload specific Parts
```

Notes:
- **Quota:** a video insert costs ~1600 of the 10,000 free daily units → ~6
  uploads/day. The run stops cleanly at quota and resumes next day.
- **Auto-set on upload:** category (Education), AI/altered-content = false,
  chapters (from the description), playlist membership, and thumbnail.
- **Manual step:** the Education **Type** dropdown (e.g. *Concept overview*) is
  Studio-only — no API exists for it, so set it by hand per video.
- Custom thumbnails require a **phone-verified** channel.

---

## Configuration

Everything tuneable lives in **`configuration.cfg`** (INI). Key knobs:

- `[pipeline]` `thread_workers` / `process_workers` — the shared IO / CPU pool sizes
  (also the default subprocess-concurrency cap). Override per run with `--workers`.
- `[manim]` `python` — the interpreter the raster renderer shells out to.
- `[ingestion]` table + code detection thresholds; code-block rendering.
- `[grouping]` `book_title` (used in Part filenames), `part_min_duration_minutes`
  (default 10), `piper_*` (narration voice), `content_groups_dir`, `approved_dir`.
- `[ollama]` `chat_model` — only the optional Part-title generator (has a fallback).
- `[ml]` embeddings endpoint/model, Random-Forest params, `code_line_threshold`,
  and `code_line_confidence_margin` (the review gate's "borderline" band).
- `[youtube]` OAuth, playlist, scheduling, pacing, thumbnail (see above).

---

## ML model training (one-time, for code detection)

Ingestion uses a Random Forest (hand-crafted features + `nomic-embed-text`
embeddings, batched) to distinguish code lines from prose:

```bash
python -m src.ingestion.ingest_pdf /path/to/book.pdf   # writes code-block candidates
python -m src.ingestion.ml.utils                       # label lines code/text
python -m src.ingestion.ml.train                       # → models/code_rf.joblib
python -m src.ingestion.ml.line_proba                  # (optional) inspect predictions
```

The review gate feeds this loop: code corrections you make there land in
`src/ingestion/ml/training_code_snippets/` and are picked up by the next `train.py`.
Without the model, code detection falls back to heuristics — usually fine, less accurate.

---

## Concurrency

One shared [`Scheduler`](src/scheduler.py) owns a **CPU pool** (PDF parsing), an
**IO pool** (grouping / manim / ffmpeg / Piper / embeddings / YouTube), and a
**global subprocess semaphore** so that, however many stages run at once
(standalone or under the orchestrator), the machine never oversubscribes. Pool
sizes come from `[pipeline]` and can be overridden per run with `--workers`.

---

## What's where

| Path | What it is |
|---|---|
| `configuration.cfg` | All tuneable settings (paths, models, YouTube) |
| `src/pipeline.py` | Orchestrator — the watchdog cascade |
| `src/scheduler.py` | Shared CPU/IO pools + subprocess semaphore |
| `src/stage_cli.py` | The `<item>` / `--all` / `--watch` / `--workers` contract |
| `src/ingestion/` | PDF → section files + extracted media; `ml/` = code classifier |
| `src/grouping/` | Grouping + listify + quote handling (`llm_grouper.py`) + review gate (`review_gate.py`) |
| `src/render/` | Scroll-canvas renderer (`build_raster.py` live path) + layout helpers (`build.py`) + Piper narration (`narrator.py`) |
| `src/assembler/` | ffmpeg merge/concat + Part packaging (`build_video.py`) |
| `src/publisher/` | describe (`describe.py`) + YouTube upload (`push_prepare.py` lib + `upload.py` CLI) |
| `pipeline/` | All generated artifacts (regenerable) |
| `media/` | Manim's raw render output (regenerable) |
| `models/` | Trained ML models (`code_rf.joblib`) |
| `FINAL_ARCH.md` | Target architecture | 
| `REFACTOR_PLAN.md` | The phased refactor that implemented it |

---

## License

See `LICENSE`.
