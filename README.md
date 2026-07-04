# VidJourney

Turn a PDF into a folder of narrated explainer videos, bundled into ~10-minute
"Part" videos and optionally published to YouTube on a schedule.

VidJourney ingests a PDF, extracts text + figures + code blocks + tables,
groups them per section, narrates everything with Piper TTS, renders each
section as a vertically-scrolling canvas with Manim + ffmpeg (camera panning in
sync with the narration), packs consecutive sections into ≥10-minute Parts, and
can upload those Parts to YouTube — scheduled, paced, and added to a playlist.

> **Architecture note:** the current pipeline is a set of standalone stages run
> in sequence (there is no single orchestrator yet — the old `main.py` watchdog
> cascade was retired). The planned re-architecture — a unified orchestrator
> with per-stage `--all`/`--watch`/`--workers` and a watchdog cascade — is
> documented in **[FINAL_ARCH.md](FINAL_ARCH.md)**.

---

## Prerequisites

- Python 3.11+ with [uv](https://docs.astral.sh/uv/)
- [ffmpeg](https://ffmpeg.org/) (with **libass** if you want burned-in subtitles)
- [Ollama](https://ollama.com) with `gemma4:e2b` pulled (chat) and
  `nomic-embed-text` (embeddings, for code detection)
- [Piper TTS](https://github.com/rhasspy/piper) voice at
  `~/.local/share/piper-voices/en_US-lessac-medium.onnx`
- `models/code_rf.joblib` — Random Forest for code-vs-text classification
  (see *ML model training* below if missing; falls back to heuristics)
- For YouTube upload only: `uv add google-api-python-client google-auth-oauthlib
  google-auth-httplib2` and an OAuth client (see *Publishing to YouTube*)

`./install.sh` handles the core dependencies (except the ML model and the
Google libraries).

---

## Pipeline at a glance

```
PDF
 │
 ▼
[1] INGEST     PDF → pipeline/sections/section_*.txt  (text, figures, code, tables)
[2] GROUP      sections → pipeline/groups/content_groups/  (deterministic grouping + listify)
[3] RENDER     per section → narrate (Piper) → scroll canvas (Manim) → ffmpeg pan
 │             → pipeline/scroll/output/section_N_raster.mp4
[4] PARTS      pack consecutive sections into ≥10-min Parts → pipeline/scroll/parts/
 │             named "<Book> - Part <n> - <Summary>.mp4"
[5] DESCRIBE   Parts → pipeline/descriptions/part_NN.md  (paste-ready YouTube metadata)
[6] PUBLISH    (optional) upload Parts to YouTube — scheduled, paced, into a playlist
```

---

## Running the pipeline

Every stage is a standalone command. You can run them **one at a time** (good
for partial reprocessing — e.g. re-render a single section after a change) or
chain them to run **all at once**.

### Individually

```bash
# [1] Ingest one PDF → section files
python -m src.ingestion.ingest_pdf /path/to/book.pdf

# [2] Group sections → content groups
python -m src.grouping.llm_grouper --all                       # all pending
python -m src.grouping.llm_grouper --watch                     # cascade: group as ingest writes files
python -m src.grouping.llm_grouper pipeline/sections/section_3.txt   # one section

# [3] Render a section → narrated scroll mp4 (the slow stage)
python -m src.render.build_raster 3                                  # one section, by number

# [4] Pack rendered sections into ≥10-min Part videos
python -m src.assembler.build_video --dry-run                        # preview the packing
python -m src.assembler.build_video                                  # write the Parts

# [5] Generate paste-ready YouTube metadata per Part
python -m src.publisher.describe --all

# [6] Upload to YouTube (optional — see Publishing to YouTube)
python -m src.publisher.upload --dry-run
python -m src.publisher.upload --limit 6
```

Note: ingest, build_video, and generate_descriptions process **everything** in
one run; group supports `--all`/`--watch`; **render is per-section** (one number
per call) — loop it to render the whole book (below).

### All at once

There is no single orchestrator yet, so a full run chains the stages (the render
step loops over every section):

```bash
PDF=/path/to/book.pdf

python -m src.ingestion.ingest_pdf "$PDF"            # 1. PDF → sections
python -m src.grouping.llm_grouper --all       # 2. sections → groups

for f in pipeline/sections/section_*.txt; do         # 3. render every section
  n=$(echo "$f" | sed -E 's/.*section_([0-9]+)\.txt/\1/')
  python -m src.render.build_raster "$n"
done

python -m src.assembler.build_video                  # 4. pack into Parts
python -m src.publisher.describe --all              # 5. YouTube metadata
# python -m src.publisher.upload --limit 6      # 6. (optional) publish
```

Tip: run the grouper in `--watch` mode in a second terminal **before** ingest,
and stages 1→2 cascade automatically as section files land. Render onward is
still manual today (the full watchdog cascade is the FINAL_ARCH.md target).

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
python -m src.publisher.upload --whoami      # confirm which channel
python -m src.publisher.upload --list        # list discovered Parts + schedule
python -m src.publisher.upload --dry-run     # full plan, no upload
python -m src.publisher.upload --limit 6     # upload N pending (≈6/day on free quota)
python -m src.publisher.upload --part 14,15  # upload specific Parts
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

- `[pipeline]` `thread_workers` / `process_workers` — concurrency pools
- `[manim]` `quality` — `qh` (1080p60), `qm` (720p30), `ql` (480p15, fastest)
- `[ingestion]` table + code detection thresholds; code-block rendering
- `[grouping]` `book_title` (used in Part filenames), `part_min_duration_minutes`
  (default 10), `piper_model`, narration/output dirs
- `[ollama]` `chat_model` (default `gemma4:e2b`) — only used for the optional
  Part-title generator
- `[ml]` embeddings endpoint/model + Random-Forest training params
- `[subtitles]` libass style (used only if subtitles are burned in)
- `[youtube]` OAuth, playlist, scheduling, pacing, thumbnail (see above)

---

## ML model training (one-time, for code detection)

Ingestion uses a Random Forest to distinguish code lines from prose. Trained
once from labeled samples:

```bash
python -m src.ingestion.ingest_pdf /path/to/book.pdf   # writes code-block candidates
python -m src.ingestion.ml.utils                       # label lines code/text
python -m src.ingestion.ml.train                       # → models/code_rf.joblib
python -m src.ingestion.ml.line_proba                  # (optional) inspect predictions
```

Without the model, code detection falls back to heuristics — usually fine, less
accurate.

---

## What's where

| Path | What it is |
|---|---|
| `configuration.cfg` | All tuneable settings (paths, models, YouTube, style) |
| `src/ingestion/` | PDF → section files + extracted media; `ml/` = code classifier |
| `src/grouping/` | Deterministic grouping + listify + quote handling |
| `src/render/` | Scroll-canvas renderer (`build_raster.py` live path) + Piper narration (`narrator.py`) |
| `src/assembler/` | ffmpeg merge/concat + Part packaging (`build_video.py`) |
| `src/publisher/` | describe (`describe.py`), YouTube upload (`push_prepare.py` + `upload.py`) |

| `pipeline/` | All generated artifacts (regenerable) |
| `media/` | Manim's raw render output (regenerable) |
| `models/` | Trained ML models (`code_rf.joblib`) |
| `FINAL_ARCH.md` | Planned re-architecture: parallel, orchestrated, dual-trigger |
| `ARCHITECTURE.md` | Stage-by-stage detail (note: predates the scroll rewrite) |

---

## License

See `LICENSE`.


## Upload

```bash
# list all parts + their scheduled dates
.venv/bin/python -m src.publisher.upload --list

# override the publish date for this run
.venv/bin/python -m src.publisher.upload --limit 6 --publish-at 2026-07-04

# which channel does the token point to
.venv/bin/python -m src.publisher.upload --whoami
```