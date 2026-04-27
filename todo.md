# VidJourney TODO

## Subtitles — future improvements

Burned-in subtitles are working (libass via the `subtitles` filter). Cards
generated per-scene from the timeline's `VOICEOVER` field, time-distributed by
word count, with per-segment anchoring for list scenes via the
`timeline_*.parts.json` sidecar.

### Sync improvements (in order of return-on-effort)

- [ ] **Smart word-duration estimation** — syllable count + punctuation pauses
  (comma ~0.25s, period ~0.5s, em-dash ~0.4s). No new dependencies. Gets
  ~70-80% of the way to forced alignment. Voice-agnostic.

  ```python
  def syllables(word: str) -> int:
      vowels = re.findall(r"[aeiouy]+", word.lower())
      return max(1, len(vowels))

  def word_weight(word: str, next_punct: str) -> float:
      base = syllables(word)
      if next_punct == ",":   return base + 0.5
      if next_punct in ".!?": return base + 1.0
      if next_punct == "—":   return base + 0.7
      return base
  ```

  Distribute scene/segment duration proportional to summed weights.

- [ ] **Forced alignment via whisper-timestamped** — exact word-level sync.
  Voice-agnostic (re-aligns automatically against actual audio).
  Cost: ~150MB whisper model + 5-10s CPU per scene.

  ```python
  import whisper_timestamped as wts
  audio = wts.load_audio(scene_wav)
  result = wts.transcribe(model, audio, language="en")
  # result["segments"][i]["words"][j] = {"text", "start", "end"}
  ```

### Style / UX

- [ ] **Per-section subtitle themes** — italic for quotes, larger for headings
- [ ] **Background box option** — `BorderStyle=3` for opaque box on busy backgrounds
- [ ] **Position-aware** — top of screen during list scenes (so summaries stay
  visible at the top), bottom for paragraphs
- [ ] **Soft subtitles option** (mov_text) — toggle-able in player, alongside or
  instead of burn-in

### Other

- [ ] **Multi-language subtitles** — whisper-timestamped supports translation,
  could generate parallel SRT tracks
- [ ] **Drift quality check** — post-render, run whisper on the final mp4 audio,
  compare to subtitle file, regenerate if drift exceeds threshold

## When voice model changes

Cache invalidation checklist:

```bash
rm -rf pipeline/groups/narration/items/         # per-item TTS cache
rm -f pipeline/groups/narration/timeline_*.wav  # scene narration cache
rm -f pipeline/groups/timelines/*.parts.json    # measured durations
rm -rf pipeline/groups/subtitles/               # subtitle cards
rm -f pipeline/output/timeline_*.mp4            # per-scene assembled mp4s
rm -f pipeline/output/section_*.mp4             # final concatenated mp4s
```

Regenerate timelines (re-narrates parts with new voice, re-measures durations),
then re-assemble. Forced alignment (when added) makes this automatic — just
re-run alignment on the new audio.

## Pipeline cleanup

- [ ] **Cache invalidation hook** — detect voice/model config changes and
  invalidate the right caches automatically (instead of manual `rm -rf`)
- [ ] **Concurrent render flakiness** — manim sometimes fails when 4 scenes
  render in parallel. Currently mitigated by 2-thread concurrency. Investigate
  whether it's a manim bug, file-locking issue, or resource contention.
- [ ] **Dangling resources policy** — currently dropped entirely. Reconsider
  if some standalone images deserve display (e.g., section opener art).
- [ ] **Image + list combination** — paragraph groups with both an image and a
  list currently show image during intro then transition to list. Works but
  the transition is a 0.5s blank moment. Could overlap fade-out and fade-in.

## Storyboard / scene quality

- [ ] **Mid-paragraph quote detection** — currently only detected when the
  whole paragraph is a quote. Often quotes are embedded mid-paragraph with
  attribution.
- [ ] **Code block syntax highlighting in narration** — code blocks display
  as images, but voiceover would benefit from "code:" prefix or skipping
  punctuation when reading code-heavy items.
- [ ] **Handle long paragraphs better** — single-paragraph scenes that run 90+
  seconds with one entity set are too static. Consider splitting into multiple
  scenes with different entity sets per paragraph chunk.
