# VidJourney — Project Context

Snapshot of the project as of 2026-05-22. Captures architecture, design decisions, current state, and open work — enough to pick up cold without scrolling chat history.

---

## What this project does

Takes a structured PDF book (currently *Designing Data-Intensive Applications*) and produces narrated educational videos. Source text → grouped into structural units (heading, paragraph, list, image, quote) → laid out → rendered to mp4 with synchronized narration.

Output target: YouTube-ready "Part" videos (~15–20 min each, packed from multiple sections).

## Two rendering architectures, both live

### A) Original "page-break" pipeline (legacy, still works)

Per-scene rendering: each content group becomes one or more scenes; each scene becomes a manim mp4 with bullet pop-in animations, fades between pages, per-scene narration files. Subtitles burned in via ffmpeg.

Files: `src/scene_grouping/llm_timeline.py` (scene builder), `src/compiler/dsl_compiler.py` (manim DSL emitter), `src/renderer/manim/manim_runner.py` (scene runner), `src/assembler/assemble.py` (per-scene assembly + section concat), `src/assembler/build_video.py` (section → Part mp4).

Output: `pipeline/output/section_N.mp4`, `pipeline/output/parts/<title>.mp4`.

**Status:** complete but superseded by the scroll architecture. Last working build at `pipeline/output/parts/Designing Data-Intensive Applications - Part 1 - Foundations of data systems.mp4` (May 20, 11:11). Kept as fallback / reference.

### B) Scroll pipeline (current production direction)

One continuous canvas per section. All content placed on a single vertical y-axis. The "video" is a camera pan over the static canvas, driven by the narration cursor.

The scroll architecture was adopted after we found that page-break tuning wouldn't converge — every fix moved bugs around rather than eliminating them. The user's framing was right: page-break has many interacting state machines (page boundaries, FADE timing, item duration, subtitle alignment); scroll collapses these into one parameter (camera y over time).

Two sub-variants exist:

**B1 — Scroll animation (manim camera move)**
`src/scroll/manim_scene.py` — MovingCameraScene that animates `camera.frame` between waypoints. Each section is one manim render, frame-by-frame.

Slow (~3 min for short sections, 20+ min for long ones) because manim re-rasterizes every mobject per frame, even though only camera y changes.

**B2 — Pre-raster + ffmpeg pan (production path)**
`src/scroll/build_raster.py` + `src/scroll/canvas_scene.py` — render the entire canvas as one tall PNG (one frame, ~30-60s), then ffmpeg `crop` + `scale` pans across it.

3-6× faster end-to-end. Same visual output (sub-pixel-smooth via 2× supersample + Lanczos downsample). Bonus: the canvas PNG itself is the "ship as notes" artifact.

---

## Scroll architecture details

### The data model

`src/scroll/blocks.py` — flat list of `Block` objects per section. Block kinds:

| Kind | Meaning | On-screen |
|---|---|---|
| `heading` | Section / chapter heading | Large centered text |
| `bullet` | One sentence (or sub-sentence after long-split) | Indented with bullet glyph by level |
| `image` | Inline figure / code block / table | Up to 6 units tall × 12 wide, with caption below |
| `quote` | Direct quotation | Italic centered with em-dash attribution |
| `paragraph` | Narrated prose (currently unused — paragraphs sentence-split into bullets instead) | n/a |

Each block carries `text` (narration), `display` (on-screen), `level` (nesting 0-2), `resource_path`, `caption`, `attribution`.

### The "verbatim display" property

**Display text = narration text, always.** No SUMMARY_PROMPT layer. No abstracted bullet summaries. Each bullet shows exactly the words that are being spoken.

This was the architectural decision after the user observed (correctly) that eye/ear divergence — bullet says one thing in 1s, audio takes 8s on a different paraphrase — forces context-switching for every block. With display=text:

- Bullets are full sentences (10-25 words typical), wrap to 2-3 lines
- Long sentences split on natural punctuation boundaries (semicolon, em-dash, comma+conjunction) into multiple sibling bullets
- LIST_ITEMs from the source show verbatim, no summarization

Architectural side benefit: **no LLM-generated content reaches the screen.** Information flows source → screen verbatim. The LLM only makes structural decisions (where to split, where there's a quote), never content decisions. Hallucination has nowhere to land.

### The unified scroll mechanism

Single rule, in [src/scroll/build.py:_camera_path](src/scroll/build.py):

> During each block's narration window, the camera moves linearly from "this block's top sits at viewport-y `CAMERA_LEAD`" to "the NEXT block's top sits at viewport-y `CAMERA_LEAD`."

Per-block scroll speed automatically = `(block_height + INTER_BLOCK_PAD) / block_duration`. Two emergent behaviors fall out of the single rule:

- **Tall blocks (images, 6u) with short narration → fast scroll** — looks like a continuous flow across the figure
- **Short blocks (bullets, 0.5-1.5u) with long narration → barely moves** — looks like the camera "settles" on each bullet

No content-type switching. No mode flags. Just one formula.

### Two-level nesting

Paragraph sentences become **level-0** bullets (•). Explicit LIST_ITEMs from the source become **level-1** sub-bullets (◦). Level 2 (▸) is supported by the renderer but currently not emitted.

Bullet glyphs and indents in `src/compiler/dsl_compiler.py` and `src/scroll/manim_scene.py`. Both stay in sync via shared constants.

### What still uses the LLM (and why it's tiny)

Only two LLM calls per section run at build time in the scroll path:

1. **`classify_paragraph`** (`src/scene_grouping/llm_classifier.py`) — quote vs concept/abstract. Only runs on paragraphs with no list_items and no resources. ~1-3 calls per section.
2. **`extract_quote`** (`src/scene_grouping/llm_quotes.py`) — pulls text + attribution from confirmed quote paragraphs. ~0-1 calls per section (only Alan Kay in section 2 hits this currently).

Both are fast (1-3s each). Could be replaced with regex if you ever wanted fully deterministic.

**Upstream**: `llm_grouper.py` builds content_groups from raw PDF text (PARAGRAPH/LIST_ITEM/IMAGE/CAPTION classification). One-time per section, cached to `pipeline/groups/content_groups/`. Scroll pipeline never re-invokes it.

### What's been DELETED from the LLM pipeline

These all ran during the page-break era; none are called by the scroll path:

- `llm_grouper.SUMMARY_PROMPT` + `_generate_list_item_summaries` (8-15 word bullet abstracts) — replaced by verbatim display
- `llm_listify` (detects implicit enumerations) — replaced by sentence-split
- `llm_list_title` (collective titles) — bullets self-document, no titles needed
- `llm_concept_cards.extract_cards` (paragraph → card titles + bodies) — replaced by sentence-split
- `_expand_list_items` in llm_timeline (long LIST_ITEM → nested fact-bullets) — replaced by verbatim + sentence-split
- `_cards_as_items` in llm_timeline (card.body → bullet) — replaced by sentence-split

Code still exists in the repo (used by the page-break path) but the scroll path bypasses all of it.

---

## Pipeline stages (scroll, raster variant)

```
PDF → sections/section_N.txt (raw text, manual step)
      ↓ llm_grouper (one-time, cached)
groups/content_groups/section_N.txt
      ↓ src.scroll.build_raster.build_section_raster
        ├─ _groups_to_blocks (deterministic + 2 LLM calls)
        ├─ _narrate_blocks (Piper TTS per block)
        ├─ _layout (natural heights + cumulative y)
        ├─ _camera_path (waypoints in world coords)
        ├─ _write_instructions → pipeline/scroll/instructions/section_N.json
        ├─ _render_canvas_png → pipeline/scroll/canvas/section_N.png  (manim, 3840 × N pixels)
        └─ _ffmpeg_pan_and_merge → pipeline/scroll/output/section_N_raster.mp4
```

Per-stage outputs:
- `pipeline/scroll/instructions/section_N.json` — block layout + camera path
- `pipeline/scroll/narration/section_N/block_NNN.wav` — per-block narration + silence chunks for image view-time
- `pipeline/scroll/narration/section_N.wav` — concatenated section narration
- `pipeline/scroll/canvas/section_N.png` — 3840 × N pre-rasterized canvas (2× supersample)
- `pipeline/scroll/output/section_N_silent.mp4` — (not used in raster path; only animation variant produces this)
- `pipeline/scroll/output/section_N_raster.mp4` — final mp4 with audio

---

## File map (scroll-related code only)

```
src/scroll/
├── __init__.py
├── blocks.py            # Block dataclass + per-kind metadata
├── build.py             # Animation-variant build (manim camera move)
├── build_raster.py      # Production build (pre-raster + ffmpeg pan)
├── canvas_scene.py      # Single-frame manim scene that lays out the whole canvas
└── manim_scene.py       # Animation manim scene (camera waypoints); also exports mobject factories used by canvas_scene
```

Key constants live near the top of `src/scroll/build.py` and are intentionally tunable:
- `INTER_BLOCK_PAD = 0.25` — gap between consecutive blocks
- `CAMERA_LEAD = 1.8` — active block sits 1.8 units above viewport center
- `IMAGE_TARGET_HEIGHT = 6.0`, `IMAGE_TARGET_WIDTH = 12.0` — image render bounds
- `BULLET_WRAP_CHARS = 70`, `BULLET_LINE_HEIGHT = 0.4` — bullet typography
- `QUOTE_WRAP_CHARS = 60`, `QUOTE_LINE_HEIGHT = 0.5` — quote typography
- `MAX_BULLET_WORDS = 25` — threshold for long-sentence splitting
- `SECONDS_PER_IMAGE_UNIT = 0.7` — synthesized view time for image-only blocks
- `SUPERSAMPLE = 2` (in build_raster.py) — pixel multiplier for pre-raster

---

## Current state of outputs

All 10 sections rendered via the raster path, plus section 10 rebuilt with two image-fix patches (width clamp + all-resources). Pending: final section 10 rebuild with both fixes is in flight as a background task.

| Section | Output | Duration | Size | Notes |
|---|---|---|---|---|
| 1 | section_1_raster.mp4 | 71s | 11 MB | OK |
| 2 | section_2_raster.mp4 | 148s | 28 MB | OK; contains Alan Kay quote |
| 3 | section_3_raster.mp4 | 233s | 48 MB | OK |
| 4 | section_4_raster.mp4 | 144s | 33 MB | OK |
| 5 | section_5_raster.mp4 | 149s | 31 MB | OK |
| 6 | section_6_raster.mp4 | 121s | 24 MB | OK |
| 7 | section_7_raster.mp4 | 132s | 28 MB | OK |
| 8 | section_8_raster.mp4 | 57s | 8 MB | OK |
| 9 | section_9_raster.mp4 | 68s | 13 MB | OK |
| 10 | section_10_raster.mp4 | 246s | (rebuilding) | Twitter section; was missing Figure 1-3 |

Plus 10 canvas PNGs in `pipeline/scroll/canvas/` — these are the ship-as-notes artifact (1.7-4.5 MB each).

---

## Major design decisions and their rationale

### 1. Scroll architecture replaces page-break
**When:** May 19-20. **Why:** Page-break had too many interacting state machines (page boundary, FADE timing, narration duration, subtitle alignment crossfading at boundaries, last-bullet-cutoff). Each fix moved the bug rather than eliminating it. Scroll has one tunable (camera y over time) and many problems disappear.

### 2. Verbatim display (no SUMMARY layer)
**When:** May 20. **Why:** User identified that eye/ear divergence forces context-switching on every block. With display=text, the visual is the audio in lockstep. Side benefit: no LLM-generated content on screen → hallucination has nowhere to land.

### 3. Pre-raster + ffmpeg pan (instead of manim animation engine)
**When:** May 21. **Why:** The animation engine was re-rasterizing every mobject every frame, even though only camera y was changing between frames. Pre-rasterize once + ffmpeg crop + Lanczos downsample gives identical visual output at 3-6× the throughput.

### 4. Two-level nesting (L0 paragraph sentences, L1 source list items)
**When:** May 20. **Why:** User asked for nested lists. The natural mapping in the source: paragraph = lead-in (L0), explicit LIST_ITEMs = enumeration (L1). Deeper nesting deferred — the source extractor doesn't capture multi-level nesting from PDFs anyway.

### 5. Image width clamp (mirroring bullet/quote clamps)
**When:** May 22. **Why:** Section 10's SQL code block (500×108, aspect 4.6:1) was rendering 27.8 units wide at `scale_to_fit_height(6)` — way past the canvas frame. Wide-aspect images now use `scale_to_fit_width(12)` with height shrinking; portrait/square unchanged.

### 6. All resources emitted (not just first)
**When:** May 22. **Why:** `resources[0]` silently dropped Figure 1-3 from section 10 group 2 (which has both Figure 1-2 and Figure 1-3 alongside one paragraph). Now iterates all resources in source order.

### 7. Canvas PNGs as a product artifact
**When:** May 22 (proposed by user). **Why:** The pre-rasterized canvas IS the section's notes. Same content as video, static. Wrappable in PDF. Tweet-embeddable. Auto-aligned with the video by construction. Compelling product extension; trivial implementation (single PNG already exists, just needs wrapping).

---

## Performance characteristics

### Per-section build time (raster path)

- Short sections (≤ 100s output): ~1.5-2 min
- Medium sections (100-200s output): ~3-5 min
- Long sections (200+s output, lots of waypoints): ~9-10 min

Cost breakdown for long sections:
- Manim single-frame canvas render: ~30-60s
- ffmpeg crop + Lanczos + crf18 encode: scales with `frames × pixels × kernel_size` — dominates for 250s+ outputs

### Comparison vs page-break (animation) era

Section 10 specifically:
- Page-break path (Apr-May): ~20-25 min wall clock for one rebuild
- Scroll animation variant: ~20+ min
- Scroll raster path: ~10 min (with current settings)
- Scroll raster path (with deferred perf tweaks): could land in ~2 min

### Easy perf wins still available

| Knob | Speedup | Cost |
|---|---|---|
| 30fps output (vs 60fps) | ~2× | Imperceptible for slow scroll |
| Bicubic scaler (vs Lanczos) | ~30% | Slight softness on fast scrolls |
| VideoToolbox HW encoder on macOS | ~2-3× | Slightly worse compression at same bitrate |
| Skip 2× supersample | ~4× | Loses sub-pixel anti-aliasing |
| Combined (30fps + bicubic + HW) | ~5-6× | Modest quality drop, probably acceptable |

Not yet applied — kept at high-quality defaults until decision.

---

## Open work

### Imminent
- **Verify section 10 rebuild** (in flight) shows Figure 1-3 properly + check Twitter section now displays both figures
- **Concat Part 1 mp4** from 10 raster mp4s (`ffmpeg concat`, ~2 min) — straightforward when section 10 lands

### Product extensions (proposed, not started)
- **Notes export** — `img2pdf` wrap of 10 canvas PNGs → Part 1 notes PDF. ~10 lines of code, ~10s runtime.
- **Per-section reading mode** — expose individual PNGs as standalone study companions

### Perf (defer until needed)
- Apply 30fps + bicubic + VideoToolbox combo when long-section render time becomes the bottleneck
- Simplify the 94-waypoint nested `if(lt(t,…))` ffmpeg expression to a constant-time piecewise function

### Legacy / cleanup
- Eventually delete the page-break pipeline (`src/scene_grouping/llm_timeline.py` scene-building, `src/compiler/dsl_compiler.py` page-break-specific code, the SUMMARY/listify/concept-card LLM modules). Currently kept as a fallback in case scroll architecture has a regression we haven't caught yet.

---

## Known gotchas

### Background tasks survive Bash tool invocations, not shell `&` + `disown`
When a Bash command is `&`'d and `disown`'d, the parent zsh process exits at turn boundary and SIGHUP's the backgrounded process. Use the Bash tool's `run_in_background=True` instead — that uses a different mechanism that survives.

### Manim resolution doesn't auto-adjust frame dimensions
Passing `--resolution=W,H` only sets pixel size; world frame dimensions stay at the manim default unless explicitly overridden in `construct()`. We set `self.camera.frame.set(height=canvas_height + padding)` in `CanvasScene` to make the frame encompass the full canvas.

### Image aspect ratios silently break wide-aspect rendering
Always clamp `scale_to_fit_height` with a subsequent width check. The bullet, quote, and now image shapes all do this. Without it, a 4:1 aspect image at height=6 ends up 24 units wide — extending past the 14.22-unit frame.

### `_groups_to_blocks` iteration over resources used to take only `resources[0]`
Now fixed to iterate all resources. Important for any future group with multiple figures.

### ffmpeg "moov atom not found" means truncated mp4
Happens when ffmpeg is killed mid-write. The mp4 has video data but the trailing index that players need to seek didn't get written. Always re-render to fix; can't repair in place.

### Disk space
`/System/Volumes/Data` was at 94% during the section 10 rebuild. Each section produces an 11-50 MB mp4 + 1.7-4.5 MB PNG. Mid-build temp files (manim's partial movies) can be GBs. Worth monitoring on long batch runs.

---

## Quick CLI reference

```bash
# Render one section via raster path
.venv/bin/python -m src.scroll.build_raster 5

# Render one section via animation path (slower, but identical output)
.venv/bin/python -m src.scroll.build 5

# Sequential rebuild of multiple sections
for sid in 3 4 5; do .venv/bin/python -m src.scroll.build_raster $sid; done

# Parallel rebuild (2 at a time, conservative for Mac)
printf '3\n4\n5\n6\n7\n8\n9\n10\n' | xargs -P 2 -I '{}' sh -c \
  '.venv/bin/python -m src.scroll.build_raster "$1"' sh '{}'

# Build content_groups for a new section (one-time per section)
.venv/bin/python -m src.scene_grouping.llm_grouper pipeline/sections/section_N.txt

# Test the long-sentence splitter
.venv/bin/python -c "from src.scroll.build import _split_long_sentence; print(_split_long_sentence('long sentence text...'))"
```

---

## Repository orientation

- `src/scene_grouping/` — content_groups extraction + (legacy) page-break timeline building
- `src/scroll/` — current scroll-based rendering pipeline
- `src/compiler/dsl_compiler.py` — manim DSL emission for the page-break path (not used by scroll)
- `src/renderer/manim/` — manim scene runner + mobject classes
- `src/narration/` — Piper TTS wrapper + Whisper alignment
- `src/assembler/` — ffmpeg wrappers
- `src/config/constants.py` — shared constants pulled from `configuration.cfg`
- `pipeline/` — all generated artifacts (sections, groups, narration, scroll, output)
