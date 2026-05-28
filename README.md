# VidJourney

Turn a PDF into a folder of narrated explainer videos.

VidJourney ingests a PDF, extracts text + figures + code blocks, picks a
visual treatment per paragraph (list / concept cards / quote / figure /
heading), narrates everything with Piper TTS, renders scenes with Manim,
burns in word-aligned subtitles, and bundles consecutive sections into
~10-minute "Part" videos ready to publish.

---

## Quick start

```bash
# 1. Install dependencies (one time)
./install.sh

# 2. Run the full pipeline on a PDF
python main.py /path/to/your.pdf

# 3. Bundle the per-section videos into 10-minute Parts
python -m src.assembler.build_video
```

Output lands in `pipeline/output/parts/`.

For the full architecture (what each stage does, every file produced, every
module), see **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## Prerequisites

- Python 3.11+ with [uv](https://docs.astral.sh/uv/)
- [ffmpeg](https://ffmpeg.org/) **with libass** (for burned-in subtitles).
  On Mac: `brew install homebrew-ffmpeg/ffmpeg/ffmpeg`
- [Ollama](https://ollama.com) with `gemma4:e2b` pulled
- [Piper TTS](https://github.com/rhasspy/piper) voice at
  `~/.local/share/piper-voices/en_US-lessac-medium.onnx`
- `models/code_rf.joblib` — Random Forest for code-vs-text classification.
  Trained from labeled samples; see *ML model training* below if missing.

`./install.sh` handles all of the above except the ML model.

---

## Pipeline at a glance

```
PDF
 │
 ▼
[1] INGEST          extract text, figures, code, tables → pipeline/sections/
[2] GROUP           LLM associates paragraphs with their resources
[3] TIMELINE        LLM picks a visual treatment per paragraph (one of:
 │                  list / concept-cards / quote / figure / heading)
 │                  Pre-narrates + forced-aligns paragraph-blank scenes.
[4] COMPILE         timeline → .scene DSL → render.json (Manim input)
[5] NARRATE         TTS audio for the remaining scenes (headings, figures)
[6] RENDER          Manim → silent mp4
[7] SUBTITLES       SRT from voiceover + alignment word-timestamps
[8] ASSEMBLE        ffmpeg merges silent mp4 + WAV + burns in subtitles
[9] CONCAT          ffmpeg-concats scenes into per-section mp4
[10] BUILD PARTS    pack consecutive sections into ≥10-min "Part" videos,
                    LLM-named: "<Book> - Part <n> - <Summary>.mp4"
```

Five visual treatments routed per paragraph:

| Content | Treatment |
|---|---|
| Heading | Full-frame text card |
| Paragraph + figure / code / table | Show the resource for the whole scene |
| Paragraph + explicit list items | Accumulating bullets with optional setup image |
| Paragraph with implicit enumeration (3+ parallel items, questions, steps) | Listify → bulleted list with a title header |
| Direct quotation | `"<quote>"` + `— <attribution>` |
| Anything else (prose) | 2-4 sequential full-frame concept cards (title + body) |

---

## Running stages independently

The pipeline is event-driven under `main.py`, but every stage also has a
standalone CLI. Useful for partial reprocessing (e.g., rerun timelines for
one section after a prompt change).

```bash
# Ingestion
python -m src.ingestion.ingest_pdf /path/to/your.pdf

# Group sections (LLM associates paragraphs with resources)
python -m src.scene_grouping.group [--all|--watch|pipeline/sections/section_N.txt]

# Build timelines (LLM picks visual treatments; pre-narrate + align paragraph-blank scenes)
python -m src.scene_grouping.llm_timeline [--all|pipeline/groups/content_groups/section_N.txt]

# Compile timeline → DSL → render.json
python -m src.compiler.compile [--all|--watch|pipeline/groups/timelines/timeline_*.txt]

# Narrate (heading and figure scenes — paragraph-blank ones are pre-narrated)
python -m src.narration.narrate [--watch|pipeline/groups/timelines/timeline_*.txt|section_N]

# Render with Manim (the slow stage — 30s to 3min per scene)
python -m src.renderer.render [--all|--watch|pipeline/render/timeline_*.render.json]

# Assemble (merge audio + video + subtitles per scene; --concat for full section)
python -m src.assembler.assemble [<scene>|section_N [--concat]|--watch]

# Build final ≥10-min Part videos
python -m src.assembler.build_video [--dry-run]
```

---

## Configuration

Everything tuneable lives in **`configuration.cfg`** (INI format). Key knobs:

- `[pipeline]` `thread_workers` (default 4) — concurrency for I/O-bound stages
- `[grouping]` `book_title` — used in Part filenames
- `[grouping]` `part_min_duration_minutes` (default 10) — minimum Part length
- `[grouping]` `max_visible_entities` (default 4) — entity-scene cap
- `[manim]` `quality` — `qh` (1080p60), `qm` (720p30), or `ql` (480p15) for speed
- `[ollama]` `chat_model` — default `gemma4:e2b`
- `[subtitles]` — libass font, color, margin (burned into the video)

---

## ML model training (one-time setup for code detection)

The ingestion stage uses a Random Forest to distinguish code lines from
prose lines. This is trained once from manually labeled samples.

```bash
# 1. Ingest a PDF (writes code-block candidates to pipeline/sections/resources/code_blocks/)
python -m src.ingestion.ingest_pdf /path/to/your.pdf

# 2. Label each line c/t (tedious — but only once)
python -m src.ingestion.ml.utils

# 3. Train (needs ~50+ labeled samples)
python -m src.ingestion.ml.train  # writes models/code_rf.joblib

# 4. Verify (optional)
python -m src.ingestion.ml.line_proba --limit 10
```

Without this model, code-block detection falls back to heuristics — usually
fine but less accurate.

---

## What's where

| Path | What it is |
|---|---|
| `main.py` | Entry point — runs all watchers in cascade |
| `configuration.cfg` | All tuneable settings (paths, models, geometry, style) |
| `src/ingestion/` | PDF → section files + extracted media |
| `src/scene_grouping/` | LLM stages: group, timeline, listify, concept cards |
| `src/narration/` | Piper TTS + faster-whisper forced alignment |
| `src/compiler/` | Timeline → DSL → Manim render JSON |
| `src/renderer/` | Manim subprocess + custom shapes (cards, pills, headings) |
| `src/subtitles/` | SRT generation with word-level timing |
| `src/assembler/` | ffmpeg merge + concat + Part packaging |
| `src/icons/` | Iconify SVG download + cache |
| `src/dsl/` | Lark grammar for the `.scene` DSL |
| `pipeline/` | All generated artifacts (regenerable) |
| `media/` | Manim's raw render output (regenerable) |
| `models/` | Trained ML models |
| `ARCHITECTURE.md` | Stage-by-stage flow with data shapes, formats, side effects |
| `ui.md` | Desktop UI design notes (PyQt6 frontend, not yet built) |

---

## License

See `LICENSE`.
