# VidJourney UI — UX Notes

Desktop application. Two screens. Goal: take a PDF in, get publishable
"Part" videos out, with the user picking which sections to include and
how to bundle them.

## Screen 1 — Empty / Upload

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                                                             │
│                    [ + Upload PDF ]                         │
│                                                             │
│                                                             │
│              (no PDF yet — drop a file or click)            │
│                                                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

Single primary action. Clicking opens a file picker; drag-and-drop also
accepted. PDF is parsed in the background — once the pipeline detects
sections, the app moves to Screen 2.

## Screen 2 — Section selection + Part packing

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Designing Data-Intensive Applications.pdf            [ + Add range ]   │
├──────────────────────────────────┬──────────────────────────────────────┤
│  PARTS TO BUILD                  │  DETECTED SECTIONS                   │
│                                  │                                      │
│   Part 1                         │   ☑ section_1   Thinking About …    │
│   From [  1 ] To [ 20 ]   [×]    │   ☑ section_2                       │
│                                  │   ☑ section_3                       │
│   Part 2                         │   ☑ section_4   Reliability         │
│   From [ 22 ] To [ 45 ]   [×]    │   ☑ section_5                       │
│                                  │   ☑ …                                │
│   Part 3                         │   ☐ section_21  (skipped)           │
│   From [ 50 ] To [ 80 ]   [×]    │   ☑ section_22                      │
│                                  │   ☑ …                                │
│                                  │                                      │
│   [ Build Parts ]                │   (224 sections total)               │
└──────────────────────────────────┴──────────────────────────────────────┘
```

### Right pane — Detected sections

- Scrollable list of every section the pipeline detected, in order.
- Each row: checkbox + section number + heading text (truncated).
- Checkbox toggles "include this section". Unchecked sections are skipped
  entirely (no timeline / render / part membership).
- Reads from `pipeline/groups/content_groups/section_*.txt` once the
  grouper stage finishes; section heading comes from the heading group.

### Left pane — Parts to build

- Each row is one Part: From/To inputs (section IDs), and an × to remove.
- Ranges may be non-contiguous (Part 1 = 1-20, Part 2 = 22-45, etc. —
  section 21 is skipped because it's unchecked or just not in any range).
- Ranges may NOT overlap. UI validates.
- `+ Add range` (top bar) appends a new Part row with empty From/To.
- `Build Parts` triggers the pipeline:
  1. Run the pipeline for every section that is (checked) AND (inside at
     least one range), skipping anything already cached on disk.
  2. Concatenate each range into a Part mp4 named via
     `build_video.py`'s LLM summary.

### State on disk

The two panes map to two persisted JSON sidecars next to the PDF:

```
pipeline/sections/section_selection.json
  { "skipped": [21, 99, 137], ... }

pipeline/sections/parts.json
  [ {"from": 1, "to": 20}, {"from": 22, "to": 45}, {"from": 50, "to": 80} ]
```

The Python pipeline reads these; the UI writes them. Headless pipeline runs
default to "all sections checked, single range covering everything".

## Behavior notes

- **Resumable**: closing the app mid-build doesn't lose work. Every stage
  (timeline / narrate / render / assemble / part) is idempotent and writes
  its output to disk before moving on. Reopening the PDF jumps straight to
  Screen 2 with the persisted selection state and current build progress.
- **Status indicators** on each section row: a small badge showing the
  stage — `pending`, `timeline ✓`, `rendered ✓`, `assembled ✓`. The Part
  rows show progress too (e.g., "12/20 sections rendered").
- **No editing inside a section** in v1. If a section's content cards
  look bad, the user has to uncheck the section. v2 could expose a card-
  editing pane.

## Doable?

Yes, straightforwardly. The whole UI is a thin shell over the existing
Python pipeline — every command we run today (`process_section`,
`compile`, `narrate`, `render`, `assemble`, `build_video`) just becomes a
subprocess call from the UI process. Stack options:

- **Electron + React** — most flexible, biggest install
- **Tauri (Rust) + React** — smaller binary, more setup
- **PyQt6 / PySide6** — single-language with the pipeline, simpler distribution
- **macOS-only SwiftUI shell** — native feel, locks platform

Default recommendation: PyQt6. The pipeline is Python, the UI process can
import the pipeline modules directly (no IPC), and PyInstaller produces a
single-file `.app` bundle. Trade-off: it'll look more "engineering tool"
than "consumer app" out of the box, but that matches the audience.
