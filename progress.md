# VidJourney — progress / handoff

A snapshot of where this project stands so the next session can pick it up
without re-reading the full transcript. Read this top-to-bottom once, then
look at the linked file paths.

## What is this project

VidJourney converts a technical book (currently *Designing Data-Intensive
Applications*, DDIA) into narrated video lectures. The input is the PDF; the
output is a set of "Part" mp4 videos, each ≥10 minutes long, named like
`Designing Data-Intensive Applications - Part 7 - Querying data systems.mp4`.

The DDIA PDF was ingested into **224 sections** (one heading-to-next-heading
chunk each). Each section is rendered as a **scroll-style video**: bullets and
images stacked vertically on a single tall canvas, narrated by Piper TTS, with
a virtual camera that scrolls down the canvas tracking the narration.

Parts are formed by **bin-packing consecutive sections** until cumulative
duration ≥ 10 min, then concatenating them. LLM titles each part based on the
section HEADINGs it contains.

## Architecture (current — the "scroll" architecture)

The pipeline lives entirely under [src/scroll/](src/scroll/) and a few
supporting modules:

```
PDF
 └─ ingest_pdf.py            →  pipeline/sections/section_N.txt    (224 files: HEADING, PARAGRAPH, LIST_ITEM, IMAGE, CAPTION)
     └─ llm_grouper.py       →  pipeline/groups/content_groups/    (LLM groups elements into heading + paragraph groups)
         └─ build_raster.py  →  pipeline/scroll/output/section_N_raster.mp4
                                  - narrate each block via Piper (cached as block_NNN_raw.wav)
                                  - pad short narrations with silence so visible content has time to be read
                                  - layout: stack blocks vertically with natural heights
                                  - camera path: linearly interpolate y over narration
                                  - render canvas PNG (manim, single-frame -s)
                                  - ffmpeg pan: crop a 1920×1080 viewport across the PNG, scale, encode
             └─ build_video.py →  pipeline/scroll/parts/<book> - Part N - <LLM title>.mp4
                                  - bin-pack sections to ≥10min parts
                                  - hard-concat with -c copy (no crossfade — narrators would overlap)
                                  - LLM-generate title from section HEADINGs
```

We **pivoted away** from the original page-break architecture (animated
manim scenes per scene) because it didn't converge — scroll has fewer
tunable knobs and produces more consistent output. **The old code is in
[archive/](archive/)** and not used; don't bring it back without good reason.

## CURRENT STATE — what's running right now

**Active background job:** `bn83e8tfe` — full 224-section rebuild with the
per-block padding fix. 3 workers, `-preset medium`, started 2026-05-28 21:43.

- Progress as of last check: **~16 / 224 sections** rebuilt with padding (latest s12-13).
- Expected wall time: **~22-31 hours total** (sections vary in length;
  averaging ~15-25 min per section in serial; 3-way parallel → ~7-10 hr,
  but the longest sections drag the tail out). Likely finishes late
  tonight or tomorrow morning.

When this finishes, run `python -m src.assembler.build_video` once. It's
**idempotent and fast** (hard-concat with stream copy):

- Detects stale parts via `_is_part_stale` (input mtime > output OR output
  not a valid yuv420p mp4) and rebuilds them.
- Existing parts in [pipeline/scroll/parts/](pipeline/scroll/parts/) are
  **stale** right now — built from un-padded sections. They'll be
  overwritten by the next `build_video.py` run.

## The pipeline's "shape" knobs (config / constants)

| Knob | Where | What it does |
|---|---|---|
| `_SEC_PER_LINE` | [src/scroll/build.py:534-540](src/scroll/build.py#L534) | Min seconds per visible line of bullet/heading/paragraph text. Drives the padding-vs-not decision. Currently 1.5s/line bullets, 1.0s/line headings, 2.0s/line quotes. |
| `SECONDS_PER_IMAGE_UNIT` | [src/scroll/build.py:135](src/scroll/build.py#L135) | View time per manim unit for image blocks without narration. 0.7s/unit. |
| `BULLET_LINE_HEIGHT` | [src/scroll/build.py:117](src/scroll/build.py#L117) | 0.4u per visible line — used for height estimation. |
| `CAMERA_LEAD` | inferred ~1.5 | How far above viewport center each block sits during its narration window. |
| `SUPERSAMPLE` | [src/scroll/build_raster.py:57](src/scroll/build_raster.py#L57) | 2× by default; drops to 1× per-section for tall canvases (`_effective_supersample`) so Cairo's 32767px surface limit isn't hit. |
| `_MAX_CAMERA_WAYPOINTS` | [src/scroll/build_raster.py:106](src/scroll/build_raster.py#L106) | 80. Douglas-Peucker simplifies the camera path down to this before generating the ffmpeg expression. |
| `GROUPING_PART_MIN_DURATION_MINUTES` | [configuration.cfg:133](configuration.cfg#L133) | 10.0 — min part length. |
| `GROUPING_BOOK_TITLE` | configuration.cfg | `"Designing Data-Intensive Applications"` — included in part filenames. |

## Recent decisions and their rationale

Listed roughly in the order they came up. All landed in the live code.

1. **Hard-cut concat for parts, not xfade.** Crossfade overlapped two
   narrators speaking simultaneously, which was unlistenable. Hard cut is
   how audiobooks/lectures behave anyway.
2. **`-pix_fmt yuv420p` is mandatory** anywhere libx264 sees an RGB input
   (PNG canvases). Without it, output is yuv444p which stutters in
   QuickTime/browsers. Applied in both `build_raster.py` and `build_video.py`.
3. **Dynamic supersample fallback.** Tall sections (over ~108 units of
   content) drop from 2× to 1× supersample because Cairo's 32767px
   surface limit kills the manim render. See `_effective_supersample`.
4. **Flat-sum gated ffmpeg expression** instead of nested `if()`.
   ffmpeg's expression parser has a nesting-depth limit (~100); long
   sections produced 130+ waypoints which crashed it. Flat sum has zero
   nesting depth.
5. **Douglas-Peucker camera-path simplification.** Even the flat-sum
   form hits an operand-count limit at ~200 terms. RDP reduces 200+
   waypoints to ≤80 with imperceptible visual change. See
   `_simplify_waypoints` in [build_raster.py](src/scroll/build_raster.py).
6. **Per-block silence padding** for rushed scroll. Bullets that wrap to
   many lines but get short narration scroll past unreadably. We compute
   `min_view = height/0.4 × SEC_PER_LINE` and pad the wav with silence to
   reach it. Audio and camera stay in lockstep. **This is what the
   current overnight rebuild is for.** Audit shows all 224 sections have
   at least one rushed block.
7. **Wav cache split**: `block_NNN_raw.wav` (Piper output, cached
   permanently) vs `block_NNN.wav` (padded version, regenerated each run
   from raw — or symlinked to raw if no pad needed). Means future rate
   tuning doesn't require re-running Piper.
8. **Section mp4 validity check in `build_video.py`.** `_is_part_stale`
   now also checks pix_fmt + readability of the existing part output, so
   half-written mp4s left by killed ffmpeg jobs get rebuilt instead of
   silently passing the mtime check.
9. **Gap-stop in bin_pack.** Removed — parts now span gaps freely once
   all sections are valid. The gap-stop was useful when half the book had
   invalid mp4s; with the current padding rebuild rebuilding everything,
   gaps will be closed by completion. (If a future run hits a real gap,
   reinstate the rule.)

## File map — what matters

Live (touched recently, will keep being touched):

- [src/scroll/build.py](src/scroll/build.py) — per-section render orchestration.
  Has `_narrate_blocks` (where the padding lives), `_layout`,
  `_camera_path`, `_groups_to_blocks`. Also has `_sentence_bullets` which
  does the listify state machine.
- [src/scroll/build_raster.py](src/scroll/build_raster.py) — the pre-raster
  + ffmpeg-pan build path. Has all four "shape" fixes (supersample,
  flat-sum, RDP, pix_fmt).
- [src/scroll/manim_scene.py](src/scroll/manim_scene.py) — manim ScrollScene
  for the legacy non-raster build path. Still works but unused.
- [src/scroll/canvas_scene.py](src/scroll/canvas_scene.py) — the single-frame
  canvas-PNG scene used by `build_raster.py`.
- [src/scroll/blocks.py](src/scroll/blocks.py) — Block dataclass.
- [src/assembler/build_video.py](src/assembler/build_video.py) — part assembly
  (bin_pack + hard-concat + LLM title).
- [src/assembler/ffmpeg_merge.py](src/assembler/ffmpeg_merge.py) — `concat_wavs`
  used by narration.
- [src/scene_grouping/llm_grouper.py](src/scene_grouping/llm_grouper.py) —
  PDF section → grouped content (LLM-driven).
- [src/scene_grouping/llm_classifier.py](src/scene_grouping/llm_classifier.py),
  [llm_quotes.py](src/scene_grouping/llm_quotes.py) — supporting LLM
  classifiers.
- [src/narration/narrator.py](src/narration/narrator.py) — Piper TTS wrapper.
- [src/ingestion/](src/ingestion/) — one-shot PDF → sections. Mostly
  read-only at this point.
- [configuration.cfg](configuration.cfg) — all the knobs.

Archive (dead code, kept for reference):

- [archive/src/](archive/src/) — old page-break pipeline modules.
- [archive/main.py](archive/main.py), [archive/rerun.py](archive/rerun.py)
  — old orchestrators.

Documentation:

- [ARCHITECTURE.md](ARCHITECTURE.md) — pre-pivot architecture doc.
- [INGESTION.md](INGESTION.md) — PDF ingestion notes (still relevant,
  ingestion didn't change).
- [Context.md](Context.md) — 340-line handoff from an earlier session
  (covers the pre-padding state).
- [ffmpeg_commands.md](ffmpeg_commands.md) — ffmpeg/ffprobe reference + the
  shell/process workflow patterns I use to debug pipelines.

## What to do when the current rebuild finishes

1. **Audit the rebuild output.** Should be 224 valid yuv420p mp4s in
   [pipeline/scroll/output/](pipeline/scroll/output/). The validation
   command:

   ```bash
   .venv/bin/python -c "
   import pathlib, subprocess
   bad = []
   for p in sorted(pathlib.Path('pipeline/scroll/output').glob('section_*_raster.mp4')):
       n = int(p.stem.split('_')[1])
       r = subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=pix_fmt','-of','csv=p=0',str(p)], capture_output=True, text=True)
       if r.stdout.strip() != 'yuv420p':
           bad.append(n)
   print('invalid:', bad if bad else 'none')"
   ```

   Any flagged sections need a re-run: `python -m src.scroll.build_raster N`.

2. **Spot-check a few section mp4s in QuickTime** — particularly s3, s10,
   s29-ish (these were the most rushed before). Scroll should now feel
   comfortable on every bullet.

3. **Build all parts:**
   ```bash
   .venv/bin/python -m src.assembler.build_video
   ```
   Fast (~1-2 min for all parts since it's stream-copy). Existing parts
   in [pipeline/scroll/parts/](pipeline/scroll/parts/) get overwritten
   because their inputs are now newer.

4. **Spot-check Part 1** — should be sections 1-5 hard-concatted. Each
   section should now end with a natural pause before the next begins
   (because the last bullet's narration was padded, giving reading time
   before the section's audio ends).

5. **If parts are good, you're done.**

## Known open items

- **Decorative images / "vignettes"** ([archive's INGESTION.md:356](archive/INGESTION.md#L356)
  notes "Decorative figures that score above the drawing-size filter
  sometimes survive. Manual delete for now."). We never wrote an
  automated raster-image filter. If you see the O'Reilly mascot animal
  appearing as a "Figure", that's why. Fix would be a size + no-caption
  heuristic in `_groups_to_blocks`.
- **Per-book tuning.** `_SEC_PER_LINE` is currently tuned for DDIA's text
  density. A different book might need a different rate. Future: maybe
  compute it from the average words-per-line of the book at narration
  time.
- **Title generation timeouts.** Some parts have fallback title
  "Continuing the journey" because the Ollama LLM call timed out while
  the renderer was also CPU-heavy. Future: serialize the LLM calls or
  retry with a longer timeout.

## How to resume in a future session

1. Read this file.
2. Check `ps aux | grep "src.scroll.build_raster" | grep -v grep | wc -l`
   to see if the rebuild is still running.
3. Check `ls pipeline/scroll/output/section_*_raster.mp4 | wc -l` — should
   be 224 when done.
4. If 224 done and no workers running, skip to "What to do when the
   current rebuild finishes" step 1.
5. If still running, just wait (or check progress with the validation
   command above to see how many are padded vs old).
