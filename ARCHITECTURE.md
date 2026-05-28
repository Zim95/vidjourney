# VidJourney — Architecture

A PDF goes in. A folder of `~10-minute "Part" videos` comes out, each with
visual scenes, AI-narrated audio, and burned-in subtitles. This doc names
every stage, every module, and every file on disk so you can trace what
happens at every step.

```
PDF
 │
 ▼
[1] INGEST          → pipeline/sections/section_N.txt
 │                    + resources (images, code, tables)
 ▼
[2] GROUP           → pipeline/groups/content_groups/section_N.txt
 │                    (LLM associates paragraphs with their resources)
 ▼
[3] TIMELINE        → pipeline/groups/timelines/timeline_section_N_scene_M.txt
 │                    (one file per scene; LLM picks visual treatment)
 │
 │ The timeline stage also runs, for paragraph-blank scenes:
 │   - narration       → pipeline/groups/narration/timeline_*.wav
 │   - alignment       → pipeline/groups/narration/timeline_*.alignment.json
 │
 ▼
[4] COMPILE         → pipeline/groups/scene_files/timeline_*.scene
 │                    pipeline/render/timeline_*.render.json
 ▼
[5] NARRATE         → pipeline/groups/narration/timeline_*.wav  (for scenes
 │                    not pre-narrated by stage 3)
 ▼
[6] RENDER (manim)  → media/videos/manim_runner/1080p60/timeline_*.mp4
 │                    (silent video)
 ▼
[7] SUBTITLES       → pipeline/groups/subtitles/timeline_*.srt
 │                    (during assemble, reads alignment sidecar)
 ▼
[8] ASSEMBLE        → pipeline/output/timeline_section_N_scene_M.mp4
 │                    (silent video + WAV + burned-in subtitles)
 ▼
[9] CONCAT SECTION  → pipeline/output/section_N.mp4
 │                    (all scenes of section N joined)
 ▼
[10] BUILD PARTS    → pipeline/output/parts/
                      <Book Title> - Part <n> - <Summary>.mp4
                      (≥10-minute bundles, LLM-named)
```

The pipeline is **event-driven** under `main.py` (watchdog on each output
directory) and **stage-by-stage** under standalone CLI calls. Both modes
draw from the same modules — only the orchestration differs.

---

## Stage 1 — Ingest (PDF → sections)

**Module:** `src/ingestion/ingest_pdf.py`
**Entry point:** `python -m src.ingestion.ingest_pdf <pdf>` *(called from `main.py:ingest()`)*

Reads the PDF with PyMuPDF (`fitz`). Detects headings, paragraphs, list items,
images, tables, code blocks. Uses a Random-Forest classifier
(`models/code_rf.joblib`) to distinguish code from text. Writes one section
per chapter / heading boundary.

**Reads:** the PDF file
**Writes:**
- `pipeline/sections/section_N.txt` — structured element stream (one line per element: `HEADING`, `PARAGRAPH`, `IMAGE <path>`, `LIST_ITEM`, `CAPTION`, etc.)
- `pipeline/sections/resources/images/` — extracted bitmaps
- `pipeline/sections/resources/code_blocks/` — extracted source files
- `pipeline/sections/resources/code_block_images/` — syntax-highlighted PNGs of code blocks
- `pipeline/sections/resources/tables/` — extracted table JSON
- `pipeline/sections/resources/drawings/` — vector drawings

---

## Stage 2 — Group (sections → content groups)

**Module:** `src/scene_grouping/llm_grouper.py` + `src/scene_grouping/group.py`
**Entry point:** `python -m src.scene_grouping.group [<section_file>|--all|--watch]`

An LLM call (Ollama, default `gemma4:e2b`) reads each section's raw elements
and groups them: a paragraph "absorbs" its associated resources (images,
code blocks, list items). Headings stay standalone. The output is a
`ContentGroup` — kind (`heading` / `paragraph` / `list` / `image` / `code_block` / `table`),
anchor element, optional resources / list_items / captions.

**Reads:** `pipeline/sections/section_N.txt`
**Writes:** `pipeline/groups/content_groups/section_N.txt`

---

## Stage 3 — Timeline (content groups → timelines)

**Module:** `src/scene_grouping/llm_timeline.py`
**Entry point:** `python -m src.scene_grouping.llm_timeline [<file>|--all]`

The heart of the visual treatment routing. Each content group becomes one
or more scenes; each scene gets its own timeline file. **Routing logic by
group kind:**

| Group kind | Visual treatment | LLM calls |
|---|---|---|
| heading | Single SHOW_HEADING (text card) | none |
| paragraph + 1 resource (image / code / table) | SHOW_RESOURCE for entire scene | none |
| paragraph + N resources | LLM splits paragraph into N segments, one per resource | 1 |
| paragraph + list_items | SHOW_LIST_ITEM for each, accumulating bullets, optional setup image | none |
| paragraph alone — implicit list | `llm_listify` detects enumerations (3+ parallel items, named concerns, sequential steps, multiple questions) → list scene with title header | 1 |
| paragraph alone — direct quote | SHOW_QUOTE with attribution | 1 |
| paragraph alone — prose (default fallback) | `llm_concept_cards` picks 2-4 KEY MOMENTS → sequential full-frame title+body cards | 1 |
| standalone image / code / table | SHOW_RESOURCE for the resource's caption | none |
| standalone list | Accumulating bullets, no intro | none |

**Side effects during this stage** (for paragraph-blank scenes only):
- `narrate_text(paragraph, scene_wav)` — pre-narrate with Piper so duration is known
- `align_narration(scene_wav, text)` — faster-whisper produces word-level timestamps
- Both written to `pipeline/groups/narration/` for the assemble + subtitles stages to reuse

**Reads:** `pipeline/groups/content_groups/section_N.txt`
**Writes:**
- `pipeline/groups/timelines/timeline_section_N_scene_M.txt` — voiceover text + event list
- `pipeline/groups/timelines/timeline_section_N_scene_M.parts.json` — per-part durations for list/concept-card scenes
- `pipeline/groups/narration/timeline_*.wav` — pre-narrated audio
- `pipeline/groups/narration/timeline_*.alignment.json` — word-level timestamps

**Sub-modules:**
- `src/scene_grouping/llm_listify.py` — detects implicit lists, generates title
- `src/scene_grouping/llm_concept_cards.py` — picks 2-4 key beats, generates title + body
- `src/scene_grouping/llm_entity.py` — kept only for quote detection now
- `src/narration/narrator.py` — Piper TTS
- `src/narration/aligner.py` — faster-whisper forced alignment

**Timeline file format:**
```
SCENE 3
TOTAL_DURATION: 79.27s
VOICEOVER: Full narration text...

TIMELINE:
  19.57s SHOW_LIST_TITLE "Key concerns" (0.5s)
  19.57s SHOW_LIST_ITEM "Reliability" (0.5s)
  37.38s SHOW_LIST_ITEM "Scalability" (0.5s)
  47.18s SHOW_LIST_ITEM "Maintainability" (0.5s)
  78.77s FADE "*" (0.5s)
```

Each timeline event has `time` (offset from scene start), `action`, optional
`target` (text / encoded payload like `title|||body`), and `duration` (animation).

---

## Stage 4 — Compile (timeline → scene file → render JSON)

**Module:** `src/compiler/dsl_compiler.py` + `src/compiler/compile.py`
**Entry point:** `python -m src.compiler.compile [<timeline_file>|--all|--watch]`

Two sub-steps:
1. **Timeline → `.scene` DSL** (`dsl_compiler.py`) — walks the event list, emits
   `ELEMENT` declarations for every visual + a `SEQUENCE` block describing the
   spawn/wait/close order. Resolves icon paths, slot positions for entities,
   width-aware positioning for cards.
2. **`.scene` → `.render.json`** (`src/dsl/parser.py` + `src/dsl/transformer.py`) —
   Lark grammar parses the DSL, transformer produces a JSON document Manim
   can consume.

**Reads:** `pipeline/groups/timelines/timeline_*.txt`
**Writes:**
- `pipeline/groups/scene_files/timeline_*.scene` — human-readable DSL
- `pipeline/render/timeline_*.render.json` — manim instruction document

---

## Stage 5 — Narrate (timelines → WAVs)

**Module:** `src/narration/narrate.py` + `src/narration/narrator.py`
**Entry point:** `python -m src.narration.narrate [<timeline_file>|section_N|--watch]`

Only generates WAVs for scenes the timeline stage didn't already pre-narrate
(headings, image-paragraph scenes, etc.). Piper TTS.

**Reads:** `pipeline/groups/timelines/timeline_*.txt` (extracts VOICEOVER field)
**Writes:** `pipeline/groups/narration/timeline_*.wav`

---

## Stage 6 — Render (render JSON → silent mp4)

**Module:** `src/renderer/render.py` + `src/renderer/manim/`
**Entry point:** `python -m src.renderer.render [<render.json>|--all|--watch]`

Forks a subprocess: `manim -qh src/renderer/manim/manim_runner.py ManimScene`.
The `ManimScene` reads the render JSON and renders each ELEMENT + SEQUENCE
step into video frames. Quality (`qh` = 1080p60) configured in `[manim]`.

**Reads:** `pipeline/render/timeline_*.render.json`
**Writes:** `media/videos/manim_runner/1080p60/timeline_*.mp4` (silent)

This is the slowest stage. 30s–3min per scene. Wall-time bottleneck for the
whole pipeline.

---

## Stage 7 — Subtitles (timeline + alignment → SRT)

**Module:** `src/subtitles/srt_writer.py`
**Entry point:** Called from the assemble stage; also `python -m src.subtitles.srt_writer <timeline>`

Generates the `.srt` cards for a scene. Reads the voiceover, chunks into ~6
word cards, and prefers timing sources in this order:

1. **Alignment sidecar** (`<wav>.alignment.json`) — word-level Whisper times
2. **Parts sidecar** (`<timeline>.parts.json`) — per-segment measured durations
3. **WAV duration** — actual audio length
4. **`TOTAL_DURATION`** — word-count estimate (last resort)

Card timings are then smoothed: each card extends to the next card's start
(no flash gaps) with a minimum 0.8s dwell (kill rush feeling).

**Reads:** timeline `.txt` + alignment `.json` + (optional) parts `.json` + WAV
**Writes:** `pipeline/groups/subtitles/timeline_*.srt`

---

## Stage 8 — Assemble (silent mp4 + WAV + SRT → final per-scene mp4)

**Module:** `src/assembler/assemble.py` + `src/assembler/ffmpeg_merge.py`
**Entry point:** `python -m src.assembler.assemble [<scene>|section_N [--concat]|--watch]`

ffmpeg merges the manim mp4 (video) with the narration WAV (audio) and burns
the SRT in via libass.

**Reads:**
- `media/videos/manim_runner/1080p60/timeline_*.mp4`
- `pipeline/groups/narration/timeline_*.wav`
- `pipeline/groups/subtitles/timeline_*.srt`

**Writes:** `pipeline/output/timeline_section_N_scene_M.mp4` — final per-scene video

---

## Stage 9 — Concat section

Same module (`assemble.py`), invoked with `section_N --concat`. After
assembling each scene, ffmpeg-concats them in order.

**Writes:** `pipeline/output/section_N.mp4` — final per-section video

---

## Stage 10 — Build Parts (final publishing-shape video)

**Module:** `src/assembler/build_video.py`
**Entry point:** `python -m src.assembler.build_video [--dry-run]`

Greedily packs consecutive `section_N.mp4` files into ≥10-minute bundles.
Each bundle gets an LLM-generated 3-6 word summary title built from the
included sections' headings. Filename: `<Book Title> - Part <n> - <Summary>.mp4`.

**Reads:** `pipeline/output/section_*.mp4` + heading text from each section's
scene_1 timeline
**Writes:** `pipeline/output/parts/<filename>.mp4`

---

## Configuration

`configuration.cfg` is the single source of truth. Loaded by
`src/config/constants.py` (uses `ConfigParser`) and exported as module-level
constants like `GROUPING_TIMELINES_DIR`, `OLLAMA_CHAT_MODEL`,
`SUBTITLE_FONT_NAME`, etc.

Major sections:
- `[pipeline]` — `thread_workers` (I/O concurrency), `process_workers` (CPU)
- `[ingestion]` — code/table detection thresholds, code rendering style
- `[grouping]` — directory paths, Piper model, canvas layout, list/card geometry, book title
- `[ollama]` — `base_url`, `chat_model`, `max_retries`
- `[subtitles]` — libass burn-in style (font, color, margin)
- `[manim]` — render quality (`qh`/`qm`/`ql`), output path, scene class
- `[ml]` — Random Forest training data location, embedding config

---

## Top-level orchestration

**`main.py`** runs the full pipeline as a cascade of watchdog watchers.
Each stage's output directory is watched; when a new file appears
downstream, the corresponding stage picks it up. The whole thing runs
inside two shared executor pools:

- `ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)` — I/O-bound stages
  (LLM calls, ffmpeg, manim subprocess)
- `ProcessPoolExecutor(max_workers=PIPELINE_PROCESS_WORKERS)` — CPU-bound
  stages (DSL compilation, embedding extraction)

```bash
uv sync
python main.py /path/to/your.pdf
```

Press `Ctrl+C` when the output is complete.

**Standalone mode** — run each stage independently via the module CLIs above.
Useful for partial reprocessing (e.g. rerun listify on one section after a
prompt change).

---

## Directory layout on disk after a full run

| Path | Contents |
|---|---|
| `pipeline/sections/section_N.txt` | Raw extracted elements (one section) |
| `pipeline/sections/resources/{images,code_blocks,tables,drawings,code_block_images}/` | Extracted media |
| `pipeline/groups/content_groups/section_N.txt` | LLM-grouped sections |
| `pipeline/groups/timelines/timeline_section_N_scene_M.txt` | Per-scene event lists |
| `pipeline/groups/timelines/timeline_*.parts.json` | Per-part duration sidecars (list / card scenes) |
| `pipeline/groups/scene_files/timeline_*.scene` | DSL source (intermediate) |
| `pipeline/render/timeline_*.render.json` | Manim instruction docs |
| `pipeline/groups/narration/timeline_*.wav` | TTS audio per scene |
| `pipeline/groups/narration/timeline_*.alignment.json` | Word-level Whisper timestamps |
| `pipeline/groups/narration/items/*.wav` | Per-card / per-list-item sub-WAVs |
| `pipeline/groups/subtitles/timeline_*.srt` | SRT subtitle cards |
| `pipeline/resources/icons/*.svg` | Downloaded Iconify icons (cached) |
| `media/videos/manim_runner/1080p60/timeline_*.mp4` | Silent rendered scenes |
| `pipeline/output/timeline_section_N_scene_M.mp4` | Assembled per-scene mp4 (audio + video + subs) |
| `pipeline/output/section_N.mp4` | Concatenated per-section mp4 |
| `pipeline/output/parts/<Book> - Part <n> - <Summary>.mp4` | Final publishing-shape mp4 |

---

## Key data shapes

**ContentGroup** (Stage 2 output, in-memory)
- `kind: str` — heading / paragraph / list / image / code_block / table
- `anchor: Element` — primary element (the paragraph text or heading text)
- `resources: list[Element]` — associated figures (paragraph groups only)
- `list_items: list[Element]` — bullet items (paragraph + list groups)
- `captions: list[Element]` — captions for figures

**TimelineEvent** (Stage 3 output, in-memory + serialized in `.txt`)
- `time: float` — when in the scene this event fires
- `action: str` — `SPAWN` / `FADE` / `SHOW_RESOURCE` / `SHOW_HEADING` / `SHOW_QUOTE` / `SHOW_LIST_ITEM` / `SHOW_LIST_TITLE` / `SHOW_CONCEPT_CARD` / `ARROW` / `HOLD`
- `target: str` — payload (resource path, text content, `"<title>|||<body>"`, `"*"` for "fade everything")
- `duration: float` — animation length

**Alignment sidecar** (Stage 3 output, JSON)
```json
[
  {"word": "When", "start": 0.04, "end": 0.21},
  {"word": "you",  "start": 0.21, "end": 0.31},
  ...
]
```

**Parts sidecar** (Stage 3 output for list / card scenes, JSON)
```json
[
  {"text": "Reliability ... <description>", "duration": 13.4},
  {"text": "Scalability ... <description>", "duration": 9.8},
  {"text": "Maintainability ... <description>", "duration": 11.2}
]
```

---

## "I'm lost — what do I read first?"

In order:

1. **This file** — for the map
2. `main.py` — entry point, executor setup, watcher cascade
3. `src/scene_grouping/llm_timeline.py:_build_paragraph_blank_scenes` — the
   routing function that decides what visual treatment a paragraph gets
4. `src/compiler/dsl_compiler.py:_compile_events` — the heart of the compile
   stage; every action type has a handler here
5. `configuration.cfg` — every tuneable knob
