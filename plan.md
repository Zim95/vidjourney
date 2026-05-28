# Plan

## Active proposal: club consecutive concept cards into a list

### What you suggested
> Whenever we have consecutive concept cards, can we club them together as list items? Maybe the paragraph of the concept card can act as a bullet point in a list. Then we come up with a collective title for them.

### What this means in practice
Today, [_build_concept_card_scene](src/scene_grouping/llm_timeline.py#L728) takes a paragraph, asks `extract_concept_cards` for ~2-5 cards, and emits them one at a time:

```
[card 1 title + body]   ← 8s, FADE
[card 2 title + body]   ← 6s, FADE
[card 3 title + body]   ← 7s, FADE
[card 4 title + body]   ← 5s, FADE
```

Each card is a full-frame screen. The viewer reads card N's body while the narrator reads card N's text, then it fades and the next card comes in. Across a long paragraph you end up watching a slideshow of 4-5 screens.

The proposal: treat the cards as a single bulleted list under one title.

```
[Collective title]                ← stays whole scene
• card 1 title                    ← spawns at 0s   (narrates card 1 body)
• card 1 title                    ← stays
• card 2 title                    ← spawns at ~8s  (narrates card 2 body)
• card 1 title
• card 2 title
• card 3 title                    ← spawns at ~14s (narrates card 3 body)
```

Mechanically: route through the existing `_build_list_scene` accumulating-bullets flow, using each concept card as a list item (`item.text = card.text`, `item.summary = card.title`). One new LLM call needed for the collective title (similar to `extract_list_title`).

### Why I think this is a good idea
- Less visual churn — one canvas the viewer fills in, instead of 4-5 separate canvases.
- Better retrospective scan — once a list is built, all the points stay visible. With cards, you only see the current one and have to remember the prior ones.
- Reuses infrastructure that already has alignment, subtitle anchoring, pagination, "(contd...)", and the new title-extraction machinery from fix #4.
- The card titles are already short, well-formed phrases (LLM is good at them) — they work as bullets without extra work.

### Things I'd want to settle before writing code
1. **Threshold.** Apply to concept-card scenes with N ≥ 3 cards? (Single card → standalone card, two cards → marginal, three+ → list.) Or always? I lean N ≥ 3 — single/double cards still look fine as full-frame.
2. **Scope.** Three places use concept cards:
   - **Standalone** (paragraph-blank routing) — clearly target for this change.
   - **Inside `_build_list_scene` case (b)** — the cards are the *intro* for a separate list. Adding a meta-list-of-cards above another list is confusing. Leave as-is.
   - **Padding in `_build_paragraph_resource_scenes`** (fix #1) — cards bridge the preamble before a figure appears. Could go either way; I'd leave as-is for now since the figure is the main visual.
3. **Collective-title generation.** New focused LLM module (`llm_concept_card_title.py`) or extend `llm_list_title.extract_list_title` to take card titles as the item summaries? The shape is identical, so probably just call the existing `extract_list_title` with `anchor=paragraph_text` and `item_summaries=[card.title for card in cards]`. No new module needed.
4. **What if a paragraph is genuinely *narrative*** (4 cards exploring different angles of one idea, no clear "collective" framing)? The title extractor would still produce *something* — and we've already seen it does that competently. But the bullets might feel forced. Worth eyeballing 3-5 examples before locking it in.

### Implementation sketch
1. In `_build_concept_card_scene`, after `extract_concept_cards`, if `len(cards) >= 3`:
   - Build pseudo-Elements: `[FakeItem(text=c.text, summary=c.title) for c in cards]`
   - Call `extract_list_title(paragraph_text, [c.title for c in cards])`
   - Delegate to `_build_list_scene(intro_text="", list_items=cards, title=collective_title, ...)`
   - Return its result.
2. If `len(cards) < 3`, keep current behavior.

Affected sections: 3 (scene 2), 4 (scene 3), 5 (scene 2), 8 (scene 2), 10 (scene 2 + scene 5). Sections 6 and 7 also have concept-card scenes (the listify-inside-list-intro case), but those are case (b) — out of scope.

Estimated scope: ~30-50 lines of code. Then a wipe + rerun of the affected sections.

---

## Pending items from the original 7-item list

| # | Item | Status |
|---|---|---|
| 6 | Per-part SRT for YouTube, stop burning subtitles into the manim mp4 | Not started |
| 7 | Voice swap from Piper to Edge TTS | Not started |

Both are independent of the concept-card-list idea — can be done in any order.

---

## Known issues to revisit

### rerun.py "Done" marker races
The `[INFO] Done. Output mp4s are in pipeline/output/` line fires after the synchronous fan-out completes, but the manim renders and assemblies are still running afterward in background watchers. We've seen this fire prematurely twice this session — section 2 got concatenated with only 3 of 4 scenes before scene 3 finished rendering. Workaround so far: manually run `assemble.py section_N --concat` after the rerun's "Done" line.

Real fix would be ~5 lines in rerun.py to actually wait for all per-section assemblies to land before printing "Done." Worth doing opportunistically if we're going to keep doing wipe + rerun cycles.

### Stale sections 5, 8, 9
Last rebuilt 2026-05-15 09:42-44, before any of the recent fixes. Don't have:
- Resource alignment (fix #1)
- Quote SHOW_QUOTE handling (fix #3) — but these sections probably don't have quote paragraphs
- Blank-intro concept cards (fix #5) — relevant if they have paragraph+list groups
- List titles + (contd...) (fix #4) — if they have paragraph+list
- New parenthetical-aware summaries (today's fix)
- Subtitle alignment for list scenes (today's drift fix)

Worth a wipe + rerun pass when we have a stable build, just to bring them up to current code.
