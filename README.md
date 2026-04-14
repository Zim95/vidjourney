# VidJourney

A tool to help me study. Converts PDFs to generated Videos.


## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for package management
- [Ollama](https://ollama.com) with models configured in `configuration.cfg` (default: `gemma4:e2b` for LLM, `nomic-embed-text` for embeddings)
- [Piper TTS](https://github.com/rhasspy/piper) voice model at `~/.local/share/piper-voices/en_US-lessac-medium.onnx`
- [ffmpeg](https://ffmpeg.org/) for audio/video merge
- [Manim Community](https://www.manim.community/) for rendering


## Pipeline Overview

```
PDF → Ingestion → Sections
                    ↓ (watchdog)
                  Content Grouping (LLM)
                    ↓ (watchdog)
                  Compile → Render
                    ↓                     ↓ (watchdog)
                  Manim Video           Narration (TTS)
                    ↓                     ↓
                         Assemble (ffmpeg)
                              ↓
                        Final Video (.mp4)
```


## Quick Start — Full Pipeline

Run everything with a single command. Starts all watchers, runs ingestion, and the pipeline cascades automatically.

```bash
uv sync
python main.py /path/to/your.pdf
```

Press `Ctrl+C` to stop when processing is complete.


## Running Modules Independently

Each module can run standalone for testing or reprocessing.

### 1. Ingestion

Reads a PDF, detects elements (headings, paragraphs, code blocks, images, tables), classifies code vs text using ML, and writes section files.

- Input: PDF file
- Output: `pipeline/sections/section_*.txt` + resources (images, code block images, tables)

```bash
python -m src.ingestion.ingest_pdf /path/to/your.pdf
```

### 2. Content Grouping

Groups section elements into content sets using an LLM (Ollama). Associates paragraphs with their related resources (images, code blocks, tables) based on semantic understanding.

- Input: `pipeline/sections/section_*.txt`
- Output: `pipeline/groups/content_groups/section_*.txt`

```bash
# Single section
python -m src.scene_grouping.group pipeline/sections/section_3.txt

# All pending sections (concurrent)
python -m src.scene_grouping.group --all

# Watch mode
python -m src.scene_grouping.group --watch
```

You can also run the grouper directly to inspect associations:

```bash
python -m src.scene_grouping.llm_grouper pipeline/sections/section_3.txt
python -m src.scene_grouping.llm_grouper --all
```

### 3. Compile

Converts timeline files into DSL `.scene` files and `.render.json` files.

- Input: `pipeline/groups/timelines/timeline_*.txt`
- Output: `pipeline/groups/scene_files/*.scene` + `pipeline/render/*.render.json`

```bash
# Single timeline
python -m src.compiler.compile pipeline/groups/timelines/timeline_section_2_scene_3.txt

# All pending
python -m src.compiler.compile --all

# Watch mode
python -m src.compiler.compile --watch
```

### 4. Render

Renders `.render.json` files with Manim to produce silent videos.

- Input: `pipeline/render/*.render.json`
- Output: `media/videos/manim_runner/480p15/*.mp4`

```bash
# Single file
python -m src.renderer.render pipeline/render/timeline_section_2_scene_3.render.json

# All pending
python -m src.renderer.render --all

# Watch mode
python -m src.renderer.render --watch
```

### 5. Narration

Generates TTS narration audio from voiceover text using Piper.

- Input: `pipeline/groups/timelines/timeline_*.txt`
- Output: `pipeline/groups/narration/*.wav`

```bash
# Single scene
python -m src.narration.narrate pipeline/groups/timelines/timeline_section_2_scene_3.txt

# All scenes for a section
python -m src.narration.narrate section_2

# Watch mode
python -m src.narration.narrate --watch
```

### 6. Assemble

Merges narration audio (.wav) and rendered video (.mp4) into the final output using ffmpeg.

- Input: `pipeline/groups/narration/*.wav` + `media/videos/manim_runner/480p15/*.mp4`
- Output: `pipeline/output/*.mp4`

```bash
# Single scene
python -m src.assembler.assemble timeline_section_2_scene_3

# All scenes for a section
python -m src.assembler.assemble section_2

# Watch mode
python -m src.assembler.assemble --watch
```

### Multi-terminal watch mode

Start all watchers in separate terminals for full event-driven processing.

```bash
# Terminal 1: Content grouping (watches sections/)
python -m src.scene_grouping.group --watch

# Terminal 2: Compiler (watches timelines/)
python -m src.compiler.compile --watch

# Terminal 3: Renderer (watches render/)
python -m src.renderer.render --watch

# Terminal 4: Narration (watches timelines/)
python -m src.narration.narrate --watch

# Terminal 5: Assembler (watches narration/)
python -m src.assembler.assemble --watch
```


## ML Model Training (Code Detection)

The ingestion pipeline uses a Random Forest model to classify lines as code or text. This model needs to be trained manually before first use. The model improves over time as you label more data and retrain.

**This is a one-time setup (and periodic retraining). It is NOT part of the main pipeline.**

### Prerequisites

- Ollama running with `nomic-embed-text` model (for embeddings)

### Step 1: Run ingestion first

You need code blocks detected by heuristics before you can label them.

```bash
python -m src.ingestion.ingest_pdf /path/to/your.pdf
```

This writes code block files to `pipeline/sections/resources/code_blocks/`.

### Step 2: Label the data (manual effort)

Go through each detected code block line by line. For each line, type `c` (code) or `t` (text). This is tedious but necessary — the model learns from your labels.

```bash
python -m src.ingestion.ml.utils
```

This reads from `pipeline/sections/resources/code_blocks/` and writes labeled JSON files to the training data directory (configured in `[ml] training_data_dir`).

- Already labeled files are skipped automatically.
- You can redo a file by typing `y` when prompted.
- You can stop anytime and resume later — progress is saved per file.

### Step 3: Train the model

Once you have enough labeled data (50+ samples recommended), train the Random Forest:

```bash
python -m src.ingestion.ml.train
```

This:
- Loads labeled data from the training data directory
- Extracts hand-crafted features + Ollama embeddings
- Runs 5-fold cross-validation and prints F1 score
- Trains the final model on all data
- Saves to `models/code_rf.joblib`

### Step 4: Check per-line probabilities (optional)

Inspect the model's confidence on individual code blocks:

```bash
# Single file
python -m src.ingestion.ml.line_proba --file pipeline/sections/resources/code_blocks/27_66_code_blocks_1.txt

# First 10 code blocks
python -m src.ingestion.ml.line_proba --limit 10
```

### Retraining

To improve the model:
1. Ingest a new PDF
2. Label the new code blocks (`python -m src.ingestion.ml.utils`)
3. Retrain (`python -m src.ingestion.ml.train`)

The more diverse your training data, the better the model generalizes.


## Configuration

All settings are in `configuration.cfg`. Key sections:

- `[ingestion]` — code detection thresholds, code block rendering style
- `[grouping]` — content groups dir, canvas layout, compiler settings
- `[ollama]` — LLM model and endpoint
- `[ml]` — ML model training, embedding config, inference threshold
