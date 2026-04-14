# VidJourney TODO

## Timeline Rework

The timeline module currently has 7 separate builders per scene type. This needs to be unified into a single visual pattern.

### Core Changes

- [ ] **Single visual pattern for all scene types** — scan voiceover word by word, spawn entity/keyword shapes at the moment the narrator says them. No different builders per scene type.
- [ ] **Sliding window eviction** — max N entities on screen (`max_on_screen` from config). When a new entity spawns and we're at capacity, fade the oldest one. FIFO eviction.
- [ ] **Screen never blank** — current visuals stay until the next scene's first entity spawns. Don't fade at scene boundaries unless the next scene has an immediate visual.
- [ ] **Arrows only for classified verbs** — use `relation_classifier.needs_arrow()` to decide if an arrow is drawn. No arrows for comparisons, attributions, descriptions.
- [ ] **SHOW_RESOURCE exception** — when a scene has a display resource (image/code block/table), show the image instead of shapes. This is the only exception to the unified pattern.

### How It Should Work

1. Extract keywords from voiceover (YAKE)
2. Find word position of each keyword in voiceover text
3. Calculate time offset: `(word_position / total_words) * narration_duration`
4. At that time: SPAWN the keyword as a shape on screen
5. If at max capacity: FADE the oldest entity first
6. If a relation exists between two on-screen entities and the verb warrants an arrow: draw it
7. At scene end: don't fade — let visuals carry over to next scene

### Example

Voiceover: "If that sounds painfully obvious, that's just because these data systems are such a successful abstraction"

```
00:00 - 03:60  narrator speaks (nothing on screen yet, previous scene's visuals still showing)
03:60          SPAWN "data systems" (keyword detected)
               → previous scene's oldest entity fades if at capacity
05:60          SPAWN "successful abstraction" (keyword detected)
               → both stay visible, carry over to next scene
```

## Other TODOs

- [ ] Improve storyboard prompt — list items not always included in voiceover
- [ ] Handle quotes deterministically — detect `—` attribution pattern in ingestion
- [ ] Icon/SVG download for concrete entities (Iconify API) — fallback to shapes
- [ ] Concatenate per-scene videos into full section video (ffmpeg concat)
- [ ] Test full pipeline end-to-end from main.py
- [ ] Tune YAKE parameters — still getting overlapping keywords like "sounds painfully" + "painfully obvious"
