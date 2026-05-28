"""
LLM-based timeline generator.

Reads content groups and produces timeline scene files. The LLM is only
called for paragraph groups with multiple resources — it decides which
resource to display alongside which sentences. All other cases are
deterministic.

Usage:
    python -m src.scene_grouping.llm_timeline pipeline/groups/content_groups/section_10.txt
    python -m src.scene_grouping.llm_timeline --all
"""
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from src.utils import logger, timer
from src.config.constants import (
    GROUPING_ANIMATION_REMOVE_TIME,
    GROUPING_ANIMATION_SPAWN_TIME,
    GROUPING_CONTENT_GROUPS_DIR,
    GROUPING_LIST_MAX_ITEMS_PER_PAGE,
    GROUPING_MIN_SCENE_DURATION,
    GROUPING_NARRATION_DIR,
    GROUPING_TIMELINES_DIR,
    GROUPING_WORDS_PER_MINUTE,
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
)
from src.scene_grouping.llm_grouper import (
    ContentGroup,
    deserialize_groups,
    RESOURCE_KINDS,
)
from src.scene_grouping.llm_quotes import extract_quote, QuoteResult
from src.scene_grouping.llm_classifier import classify_paragraph
from src.scene_grouping.llm_list_title import extract_list_title
from src.scene_grouping.llm_listify import listify as listify_paragraph
from src.scene_grouping.llm_concept_cards import extract_cards as extract_concept_cards
from src.narration.narrator import narrate_text, get_audio_duration
from src.narration.aligner import align_narration
from src.assembler.ffmpeg_merge import concat_wavs


CONTENT_GROUPS_DIR = GROUPING_CONTENT_GROUPS_DIR
TIMELINES_DIR = GROUPING_TIMELINES_DIR

WPM = GROUPING_WORDS_PER_MINUTE
MIN_DURATION = GROUPING_MIN_SCENE_DURATION
SPAWN_DURATION = GROUPING_ANIMATION_SPAWN_TIME
FADE_DURATION = GROUPING_ANIMATION_REMOVE_TIME
LIST_MAX_ITEMS_PER_PAGE = GROUPING_LIST_MAX_ITEMS_PER_PAGE


# --- Data structures ---

@dataclass
class TimelineEvent:
    time: float
    action: str      # SHOW_RESOURCE, SHOW_TEXT, FADE, HOLD
    target: str      # resource path, text content, "*", or ""
    duration: float
    level: int = 0   # nesting depth for SHOW_LIST_ITEM (0 = top, 1 = sub, 2 = sub-sub)


@dataclass
class Scene:
    scene_id: int
    voiceover: str
    events: list[TimelineEvent] = field(default_factory=list)
    duration: float = 0.0


# Max nesting depth supported by the renderer. The user accepted that
# arbitrary nesting would eventually run out of horizontal space, so we cap
# at 3 levels (0, 1, 2) and use a different bullet glyph per level.
MAX_NESTING_LEVEL = 2


@dataclass
class _CardItem:
    """Adapter that lets a concept card flow through ``_build_list_scene`` as
    a list bullet. Has the ``.text`` (narration) and ``.summary`` (on-screen
    label) attributes both ``Element`` and ``ListifyItem`` expose, plus a
    ``level`` for nested-list rendering.
    """
    text: str
    summary: str
    level: int = 0


# --- Utilities ---

def _duration(text: str) -> float:
    words = len(text.split())
    seconds = (words / WPM) * 60.0
    return round(max(MIN_DURATION, seconds), 1)


def _split_sentences(text: str) -> list[str]:
    # PDF extraction gives mid-word soft-hyphens with a following space/newline
    # (e.g. "espe\u2010 cially" \u2192 should be "especially"). Strip hyphen-plus-whitespace
    # together; previously we only stripped the hyphen, leaving "espe cially".
    text = re.sub(r"[\u2010\u00ad]\s+", "", text)
    # Strip any remaining bare soft-hyphens / U+2010 that aren't followed by whitespace.
    text = text.replace("\u00ad", "").replace("\u2010", "")
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [s.strip() for s in sentences if s.strip()]


def _strip_bullet(text: str) -> str:
    return re.sub(r"^[•·\-*]\s*", "", text).strip()


_FIGURE_RE = re.compile(r"Figure\s+([\d]+(?:[-‐‑‒–—.][\d]+)*)", re.IGNORECASE)


def _figure_id(text: str) -> str | None:
    """Return the normalized 'Figure N' token from text, or None."""
    m = _FIGURE_RE.search(text or "")
    if not m:
        return None
    return f"Figure {m.group(1)}"


def _assign_resources_to_sentences(
    paragraph_text: str,
    resources: list,
    cap_map: dict,
) -> list[dict]:
    """Deterministically bind each resource to a sentence in the paragraph.

    Rules:
    - A resource whose caption names a "Figure N" binds to the first sentence
      that also names "Figure N". If the paragraph never names it, the resource
      is not shown.
    - A resource without an identifying caption (e.g. an inline code block) is
      treated as a lead-in: bound to the last sentence in the paragraph that
      isn't already bound.
    - Sentences without a direct binding inherit the previous binding so the
      visual stays put while the narrator continues the same topic.
    - Sentences before any binding are returned with ``resource_index=None``,
      meaning the caller should fill the window with concept cards.

    Returns segment dicts ``{"text": <consecutive sentences>, "resource_index": int | None}``,
    one per contiguous binding run.
    """
    sentences = _split_sentences(paragraph_text)
    if not sentences:
        return []
    n = len(sentences)
    bindings: list[int | None] = [None] * n

    # Pass 1: bind resources whose caption names a "Figure N" found in the paragraph.
    captioned_indices: set[int] = set()
    for r_idx, res in enumerate(resources):
        caption = cap_map.get(res.text, "") or ""
        fig = _figure_id(caption)
        if fig is None:
            continue
        captioned_indices.add(r_idx)
        # Match against the same normalized form on the sentence side
        for s_idx, sent in enumerate(sentences):
            if _figure_id(sent) == fig:
                if bindings[s_idx] is None:
                    bindings[s_idx] = r_idx
                break
        # If never mentioned, the resource is intentionally dropped.

    # Pass 2: bind uncaptioned resources to the last unbound sentence (lead-in heuristic).
    for r_idx, res in enumerate(resources):
        if r_idx in captioned_indices:
            continue
        for s_idx in range(n - 1, -1, -1):
            if bindings[s_idx] is None:
                bindings[s_idx] = r_idx
                break

    # Pass 3: inherit previous binding through sentences that have none.
    last_binding: int | None = None
    for s_idx in range(n):
        if bindings[s_idx] is None:
            bindings[s_idx] = last_binding
        else:
            last_binding = bindings[s_idx]

    # Group consecutive sentences sharing the same binding.
    segments: list[dict] = []
    cur = bindings[0]
    start = 0
    for s_idx in range(1, n):
        if bindings[s_idx] != cur:
            segments.append({
                "text": " ".join(sentences[start:s_idx]),
                "resource_index": cur,
            })
            cur = bindings[s_idx]
            start = s_idx
    segments.append({
        "text": " ".join(sentences[start:n]),
        "resource_index": cur,
    })

    # Roll tiny "preamble" segments forward — a leading list marker like "2."
    # gets isolated as its own sentence by the splitter but isn't worth a
    # standalone concept-card scene. Merge it into whatever scene follows.
    merged: list[dict] = []
    for seg in segments:
        if (merged
                and merged[-1]["resource_index"] is None
                and len(merged[-1]["text"].split()) < 4):
            seg = {"text": merged[-1]["text"] + " " + seg["text"], "resource_index": seg["resource_index"]}
            merged.pop()
        merged.append(seg)
    return merged


# --- LLM call ---

PROMPT = """\
Given a paragraph and its associated resources, split the paragraph text into \
consecutive scene segments. Each segment will be narrated while displaying one \
specific resource.

Paragraph:
"{paragraph}"

Resources:
{resources}

Output valid JSON only:
{{"scenes": [{{"text": "<exact consecutive sentences from paragraph>", "resource_index": <int>}}, ...]}}

Rules:
- Each scene's text must be exact consecutive sentences from the paragraph.
- Together, all scene texts must cover the entire paragraph. Do not drop or add text.
- Each scene gets exactly one resource_index to display.
- A resource can appear in multiple consecutive scenes if needed.
- If the paragraph only references one resource, put all text in one scene with that resource."""


def _call_ollama(prompt: str) -> str:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": 16384, "temperature": 0},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _build_resource_prompt(group: ContentGroup) -> str:
    cap_map = group.caption_for_resource
    resources = group.resources
    lines = []
    for i, res in enumerate(resources):
        caption = cap_map.get(res.text, "(no caption)")
        lines.append(f"[{i}] {res.kind} {res.text} — \"{caption}\"")
    return PROMPT.format(
        paragraph=group.anchor.text,
        resources="\n".join(lines),
    )


def _coerce_resource_index(raw, default: int = 0) -> int:
    """Coerce LLM-returned resource_index to int, accepting int, str, or list of int."""
    if raw is None:
        return default
    if isinstance(raw, list):
        return _coerce_resource_index(raw[0], default) if raw else default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _parse_llm_scenes(response_text: str, resources: list) -> list[dict]:
    data = json.loads(response_text)
    scenes = data.get("scenes", data)
    result = []
    for s in scenes:
        text = s.get("text", "").strip()
        r_idx = _coerce_resource_index(s.get("resource_index"))
        if text and 0 <= r_idx < len(resources):
            result.append({"text": text, "resource_index": r_idx})
    return result


def _structural_resource_fallback(paragraph_text: str, resources: list) -> list[dict]:
    """Split sentences evenly across resources."""
    sentences = _split_sentences(paragraph_text)
    if not sentences:
        return [{"text": paragraph_text or "", "resource_index": 0}]

    chunk_size = max(1, math.ceil(len(sentences) / len(resources)))
    result = []
    for i, res_idx in enumerate(range(len(resources))):
        chunk = sentences[i * chunk_size:(i + 1) * chunk_size]
        if chunk:
            result.append({"text": " ".join(chunk), "resource_index": res_idx})

    # Any remaining sentences go to the last resource
    remaining = sentences[len(resources) * chunk_size:]
    if remaining and result:
        result[-1]["text"] += " " + " ".join(remaining)

    return result


# --- Scene builders ---

def _build_heading_scenes(group: ContentGroup, scene_id: int) -> list[Scene]:
    text = group.anchor.text
    dur = _duration(text)
    return [Scene(
        scene_id=scene_id,
        voiceover=text,
        duration=dur,
        events=[
            TimelineEvent(time=0.0, action="SHOW_HEADING", target=text, duration=round(dur - FADE_DURATION, 1)),
            TimelineEvent(time=round(dur - FADE_DURATION, 1), action="FADE", target="*", duration=FADE_DURATION),
        ],
    )]


def _build_paragraph_resource_scenes(
    group: ContentGroup, scene_id: int, section_name: str,
) -> list[Scene]:
    """Bind each resource to the sentence that names it (caption-anchored).

    Sentences with no resource binding play as concept cards (same shape as
    ``_build_concept_card_scene``) so the screen never sits blank while the
    narrator is mid-paragraph. Captioned resources only appear once the
    narration actually references their figure id; resources whose caption is
    never named in the paragraph are dropped rather than slotted in arbitrarily.
    """
    resources = group.resources
    cap_map = group.caption_for_resource

    segments = _assign_resources_to_sentences(group.anchor.text, resources, cap_map)
    if not segments:
        return []

    scenes: list[Scene] = []
    for seg in segments:
        text = seg["text"]
        r_idx = seg["resource_index"]

        if r_idx is None:
            # Preamble (or gap) — render as concept cards.
            new_scenes = _build_concept_card_scene(text, scene_id, section_name)
            if new_scenes:
                scenes.extend(new_scenes)
                scene_id = scenes[-1].scene_id + 1
            continue

        res = resources[r_idx]
        caption = cap_map.get(res.text) or ""
        target = f"{res.text}|||{caption}" if caption else res.text
        dur = _duration(text)
        scenes.append(Scene(
            scene_id=scene_id,
            voiceover=text,
            duration=dur,
            events=[
                TimelineEvent(time=0.0, action="SHOW_RESOURCE", target=target, duration=SPAWN_DURATION),
                TimelineEvent(time=round(dur - FADE_DURATION, 1), action="FADE", target="*", duration=FADE_DURATION),
            ],
        ))
        scene_id += 1

    return scenes


def _build_paragraph_list_scenes(
    group: ContentGroup,
    scene_id: int,
    section_name: str,
) -> list[Scene]:
    """Render a paragraph+list group as one or two scenes.

    Three behaviors based on what's in the group:

    1. Paragraph + resource + items → ONE scene: narrate intro while the
       resource is on screen, then accumulate list items beneath. The
       resource is the primary visual; nothing else gets demoted alongside it.

    2. Paragraph (substantive) + items (no resource) → TWO scenes:
       Phase 1 is a concept-card-list scene built from the intro paragraph
       (it gets its own collective title and bullet stack). It fades out
       before phase 2 — the real LIST_ITEMs — appears under its own title.
       Keeping the preamble visually separate from the enumeration was the
       user's request: the previous unified-list shape made it hard to tell
       which bullets were "lead-in" vs which were the actual list.

    3. Paragraph (truncated / not a complete lead-in) + items → ONE scene
       (real items only). Truncated anchors like "...switched to approach"
       produce ellipsis-laden concept cards that read as garbage, so we skip
       the preamble scene entirely in that case. Heuristic: the anchor must
       end in a sentence-ending or list-introducing punctuation mark
       (``.!?:``) to be considered a complete lead-in worth summarizing.
    """
    intro_text = group.anchor.text.strip()
    list_items = list(group.list_items)
    intro_resources = group.resources
    cap_map = group.caption_for_resource

    # Case 1: paragraph + resource → single-scene flow. Apply long-item
    # expansion so any verbose LIST_ITEMs here get fanned out the same way.
    if intro_resources:
        expanded_items = _expand_list_items(list_items)
        item_summaries = [it.summary for it in expanded_items if it.level == 0]
        title = extract_list_title(intro_text, item_summaries)
        return _build_list_scene(
            scene_id=scene_id,
            section_name=section_name,
            intro_text=intro_text,
            list_items=expanded_items,
            intro_resources=intro_resources,
            intro_caption_map=cap_map,
            title=title,
        )

    scenes: list[Scene] = []

    # Case 2 (preamble phase): emit only if the anchor is a complete lead-in.
    # We use the trailing punctuation as a cheap signal for "this is a full
    # introductory paragraph" vs "this is a truncated sentence the grouper
    # severed mid-clause" (section 10 group 3: "...switched to approach").
    anchor_is_complete = intro_text and intro_text[-1] in ".!?:"
    if anchor_is_complete:
        preamble_scenes = _build_concept_card_scene(intro_text, scene_id, section_name)
        if preamble_scenes:
            scenes.extend(preamble_scenes)
            scene_id = scenes[-1].scene_id + 1

    # Case 2/3 (items phase): real LIST_ITEMs as their own list scene with
    # their own title. ``intro_text=""`` so no second narration of the
    # preamble — phase 1 already covered it. Long items get expanded into
    # level-0 parent + level-1 fact-bullet children so concrete content
    # (numbers, names, key relationships) shows up on screen instead of
    # being collapsed into a 3-word abstract.
    expanded_items = _expand_list_items(list_items)
    item_summaries = [it.summary for it in expanded_items if it.level == 0]
    title_anchor = intro_text or " ".join(item_summaries)
    title = extract_list_title(title_anchor, item_summaries)
    items_scenes = _build_list_scene(
        scene_id=scene_id,
        section_name=section_name,
        intro_text="",
        list_items=expanded_items,
        intro_resources=[],
        intro_caption_map={},
        title=title,
    )
    scenes.extend(items_scenes)
    return scenes


def _build_standalone_list_scenes(
    group: ContentGroup,
    scene_id: int,
    section_name: str,
) -> list[Scene]:
    """List with no paragraph anchor — same accumulation behavior, just no intro."""
    return _build_list_scene(
        scene_id=scene_id,
        section_name=section_name,
        intro_text="",
        list_items=group.list_items,
        intro_resources=[],
        intro_caption_map={},
    )


def _build_list_scene(
    scene_id: int,
    section_name: str,
    intro_text: str,
    list_items: list,
    intro_resources: list,
    intro_caption_map: dict,
    title: str = "",
) -> list[Scene]:
    """Build a scene with an accumulating bulleted list, optionally preceded
    by a narrated intro paired with a resource.

    Two sub-cases:

    (a) ``intro_text`` and ``intro_resources`` both present — narrate the
        intro as one chunk while the resource (image / table / code block)
        is on screen, then fade and start the list items.

    (b) No ``intro_text`` (or no resources) — go straight to list items. The
        caller is responsible for converting any prose intro into prepended
        list items beforehand (see ``_build_paragraph_list_scenes``).

    The bullet stack accumulates as items spawn. Once it hits
    ``LIST_MAX_ITEMS_PER_PAGE`` bullets the page fades and a new one starts;
    a non-empty ``title`` reappears as the header on every page, with
    ``(contd...)`` appended on page 2+ so the viewer always knows what's
    being enumerated.
    """
    if not list_items:
        return []

    scene_stem = f"timeline_{section_name}_scene_{scene_id}"
    items_dir = GROUPING_NARRATION_DIR / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

    # --- Intro pre-narration (case a only) ---
    intro_part_wavs: list[Path] = []
    intro_part_durations: list[float] = []
    intro_parts_meta: list[dict] = []

    if intro_text and intro_resources:
        intro_wav = items_dir / f"{scene_stem}_intro.wav"
        if not intro_wav.exists():
            narrate_text(intro_text, intro_wav)
        intro_part_wavs.append(intro_wav)
        intro_part_durations.append(get_audio_duration(intro_wav))
        intro_parts_meta.append({"text": intro_text, "duration": intro_part_durations[0]})

    # --- List item pre-narration ---
    item_texts: list[str] = []
    item_wavs: list[Path] = []
    item_durations: list[float] = []
    for i, item_el in enumerate(list_items):
        text = _strip_bullet(item_el.text)
        item_texts.append(text)
        item_wav = items_dir / f"{scene_stem}_item_{i + 1}.wav"
        if not item_wav.exists():
            narrate_text(text, item_wav)
        item_wavs.append(item_wav)
        item_durations.append(get_audio_duration(item_wav))

    # Combined wav list — used for concatenation into the scene wav.
    part_wavs = intro_part_wavs + item_wavs

    # --- Compose events ---
    events: list[TimelineEvent] = []
    cumulative = 0.0
    intro_dur = sum(intro_part_durations)

    if intro_text and intro_resources:
        # Case (a): resource shown 0 → intro_dur, fades when first list item spawns
        intro_resource = intro_resources[0]
        caption = intro_caption_map.get(intro_resource.text) or ""
        target = f"{intro_resource.text}|||{caption}" if caption else intro_resource.text
        events.append(TimelineEvent(
            time=0.0,
            action="SHOW_RESOURCE",
            target=target,
            duration=SPAWN_DURATION,
        ))
        events.append(TimelineEvent(
            time=round(intro_dur, 2),
            action="FADE",
            target="*",
            duration=FADE_DURATION,
        ))

    cumulative = intro_dur

    # --- List items, paginated ---
    #
    # Items beyond LIST_MAX_ITEMS_PER_PAGE would shrink to fit on screen —
    # instead split into pages and FADE * between them. Title (if any)
    # reappears on every page so the viewer always knows what they're
    # looking at.
    for page_start in range(0, len(list_items), LIST_MAX_ITEMS_PER_PAGE):
        page = list_items[page_start : page_start + LIST_MAX_ITEMS_PER_PAGE]

        if page_start > 0:
            events.append(TimelineEvent(
                time=round(cumulative, 2),
                action="FADE",
                target="*",
                duration=FADE_DURATION,
            ))

        if title:
            page_title = title if page_start == 0 else f"{title} (contd...)"
            events.append(TimelineEvent(
                time=round(cumulative, 2),
                action="SHOW_LIST_TITLE",
                target=page_title,
                duration=SPAWN_DURATION,
            ))

        for j, item_el in enumerate(page):
            summary = (item_el.summary or _strip_bullet(item_el.text)).strip()
            level = max(0, min(MAX_NESTING_LEVEL, getattr(item_el, "level", 0)))
            events.append(TimelineEvent(
                time=round(cumulative, 2),
                action="SHOW_LIST_ITEM",
                target=summary,
                duration=SPAWN_DURATION,
                level=level,
            ))
            global_idx = page_start + j
            cumulative += item_durations[global_idx]

    total_duration = round(cumulative, 2)
    fade_at = round(total_duration - FADE_DURATION, 2)
    events.append(TimelineEvent(time=fade_at, action="FADE", target="*", duration=FADE_DURATION))

    # Concatenate per-part WAVs into the scene's narration file
    scene_wav = GROUPING_NARRATION_DIR / f"{scene_stem}.wav"
    if not scene_wav.exists():
        concat_wavs(part_wavs, scene_wav)

    # Voiceover: intro narration (case a) then list items.
    voiceover_parts = [intro_text] if intro_text else []
    voiceover_parts.extend(item_texts)
    voiceover = " ".join(voiceover_parts).strip()

    # Run alignment so the subtitle generator has word-level timestamps for
    # the whole scene — same treatment concept-card and quote scenes get.
    # Without this, SRT generation falls back to word-rate distribution
    # within each part and visibly drifts on longer scenes.
    align_narration(scene_wav, voiceover)

    # Parts sidecar — used by the subtitle generator to anchor subtitle chunks
    # inside each part's measured audio window. Intro parts (whether a single
    # narration chunk or N card chunks) come first, list items after.
    parts_meta: list[dict] = list(intro_parts_meta)
    for i, text in enumerate(item_texts):
        parts_meta.append({"text": text, "duration": item_durations[i]})

    sidecar_path = TIMELINES_DIR / f"{scene_stem}.parts.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(parts_meta, indent=2), encoding="utf-8")

    return [Scene(
        scene_id=scene_id,
        voiceover=voiceover,
        duration=total_duration,
        events=events,
    )]


def _build_paragraph_blank_scenes(
    group: ContentGroup, scene_id: int, section_name: str,
) -> list[Scene]:
    """Build scenes for a paragraph with no resources/list items.

    Three paths, in priority order:

    1. Listify — if the paragraph hides an enumeration, reroute to the list
       scene flow with intro + accumulating bullets + title header.
    2. Classify (LLM) — focused one-shot classifier returns ``quote``,
       ``concept``, or ``abstract``. We then dispatch to a focused extractor
       for that case. Splitting classification from extraction keeps each
       LLM prompt short and accurate. (Historically a single ``extract``
       did both classification and entity extraction; splitting them gave
       a measurable accuracy bump for quote-shaped paragraphs.)
       - quote     → ``extract_quote`` → single SHOW_QUOTE event.
       - concept   → concept-card scene (default).
       - abstract  → concept-card scene as well (never leave the screen
                     blank during narration; concept cards are the safe
                     visual fallback).
    """
    text = group.anchor.text.strip()
    if not text:
        return []

    # Path 1: listify
    listified = listify_paragraph(text)
    if listified.should_listify():
        items = list(listified.items)
        if listified.outro:
            items[-1] = type(items[-1])(
                text=f"{items[-1].text} {listified.outro}".strip(),
                summary=items[-1].summary,
            )
        return _build_list_scene(
            scene_id=scene_id,
            section_name=section_name,
            intro_text=listified.intro,
            list_items=items,
            intro_resources=[],
            intro_caption_map={},
            title=listified.title,
        )

    # Path 2: classify, then dispatch.
    verdict = classify_paragraph(text)

    if verdict == "quote":
        return _build_quote_scene(text, scene_id, section_name)

    # concept / abstract → concept cards
    return _build_concept_card_scene(text, scene_id, section_name)


def _build_quote_scene(
    paragraph_text: str, scene_id: int, section_name: str,
) -> list[Scene]:
    """Pre-narrate the quote, then build a single SHOW_QUOTE scene whose
    fade-out lines up with the measured audio length.

    Mirrors the concept-card pre-narration pattern: TTS the actual voiceover
    text, read the wav duration off disk, use that to drive the timeline.
    Without this, the fade fires at a word-count estimate which can be
    several seconds off the real narration — the quote disappears before
    the narrator finishes (or sits on a blank tail of silence afterward).
    Also runs forced alignment so the burned-subtitle / external-srt path
    has accurate word-level timings.
    """
    quote = extract_quote(paragraph_text)
    scene_stem = f"timeline_{section_name}_scene_{scene_id}"
    scene_wav = GROUPING_NARRATION_DIR / f"{scene_stem}.wav"

    if not scene_wav.exists():
        narrate_text(paragraph_text, scene_wav)

    measured = get_audio_duration(scene_wav)
    duration = round(max(MIN_DURATION, measured or _duration(paragraph_text)), 2)

    # Run alignment so the subtitle generator has word-level timestamps for
    # the whole quote — same treatment concept cards get.
    align_narration(scene_wav, paragraph_text)

    fade_at = round(duration - FADE_DURATION, 2)
    target = quote.text if not quote.attribution else f"{quote.text}|||{quote.attribution}"
    events = [
        TimelineEvent(time=0.0, action="SHOW_QUOTE", target=target, duration=fade_at),
        TimelineEvent(time=fade_at, action="FADE", target="*", duration=FADE_DURATION),
    ]

    return [Scene(
        scene_id=scene_id,
        voiceover=paragraph_text,
        duration=duration,
        events=events,
    )]


def _cards_as_items(paragraph_text: str, level: int = 0) -> list[_CardItem]:
    """Run the concept-card extractor and adapt each card to a list-item
    shape. The card's full ``text`` (the prose chunk) is narrated; the card's
    ``body`` (the 1-2 sentence display summary) becomes the on-screen bullet.

    Using ``body`` rather than ``title`` here is intentional: card titles are
    noun-phrase headlines (designed for a full-frame slide title) and read
    too "title-y" when demoted to bullets. Bodies are written as proper
    descriptive sentences, which fit the bullet register.

    ``level`` is propagated to each returned ``_CardItem`` so callers can
    place these bullets at a nested indent (used by long-LIST_ITEM expansion
    into level-1 sub-bullets).

    Returns ``[]`` when extraction produces no cards.
    """
    result = extract_concept_cards(paragraph_text)
    if not result.cards:
        return []
    return [_CardItem(text=c.text, summary=c.body, level=level) for c in result.cards]


# A LIST_ITEM whose source text is longer than this gets fanned out into
# nested fact-bullets so the visual list preserves the concrete content
# (numbers, names, key comparisons). Shorter items render as a single
# level-0 bullet using their existing summary.
_LIST_ITEM_EXPANSION_THRESHOLD_WORDS = 30


def _expand_list_items(list_items: list) -> list:
    """Convert a flat list of LIST_ITEM Elements into a flat list of bullets
    with nesting levels, replacing long items with a level-0 parent +
    level-1 fact-bullet children.

    Twitter is the motivating case: each LIST_ITEM is 60-100 words of prose
    with concrete numbers ("75 followers", "345k writes/sec", "30M followers")
    that get crushed into a 3-word summary like "Prefer work at write time".
    By expanding long items into sub-bullets via ``extract_concept_cards``,
    each fact gets its own bullet under the parent header.

    Short items (< ``_LIST_ITEM_EXPANSION_THRESHOLD_WORDS``) pass through
    unchanged at level 0 — they were already concise enough that the summary
    captures the point.
    """
    expanded: list = []
    for item in list_items:
        body = _strip_bullet(item.text)
        word_count = len(body.split())
        summary = (item.summary or body).strip()

        if word_count <= _LIST_ITEM_EXPANSION_THRESHOLD_WORDS:
            # Short item: single level-0 bullet. Narration is the original
            # text so the audio still reads it verbatim.
            expanded.append(_CardItem(text=body, summary=summary, level=0))
            continue

        # Long item: fan out into level-1 fact-bullets. The parent appears as
        # the level-0 header (summary only, no narration of its own since the
        # children's texts together cover the source verbatim).
        children = _cards_as_items(body, level=1)
        if not children:
            # Card extraction failed — fall back to a single level-0 bullet so
            # the item still gets its narration and a bullet on screen.
            expanded.append(_CardItem(text=body, summary=summary, level=0))
            continue

        # Parent header bullet. Narrate the summary itself so this bullet has
        # its own short audio window (~2s) — gives the viewer time to read
        # the header before sub-bullets start spawning. Source-narration
        # purity is sacrificed for legibility; the children's narrations
        # together still cover the rest of the LIST_ITEM verbatim.
        expanded.append(_CardItem(text=summary, summary=summary, level=0))
        expanded.extend(children)
    return expanded


def _build_concept_card_scene(
    paragraph_text: str, scene_id: int, section_name: str,
) -> list[Scene]:
    """Render a concept-card paragraph as an accumulating bulleted list.

    Each card produced by ``extract_concept_cards`` becomes one bullet — the
    card's title is the on-screen label, the body is the narration. A
    collective title is generated via ``extract_list_title`` so the bullet
    stack has a header. Delegates to ``_build_list_scene`` so it inherits
    the pre-narration, alignment, parts-sidecar, pagination, and
    ``(contd...)`` behavior from the list flow.

    This replaced the prior sequential SHOW_CONCEPT_CARD pattern (one card at
    a time, fade between them). The user feedback was that the cards
    appeared too often and felt like cycling slides; bullets accumulate
    instead, so the viewer can scan everything at the end.
    """
    items = _cards_as_items(paragraph_text)
    if not items:
        return []

    title = extract_list_title(paragraph_text, [it.summary for it in items])
    return _build_list_scene(
        scene_id=scene_id,
        section_name=section_name,
        intro_text="",
        list_items=items,
        intro_resources=[],
        intro_caption_map={},
        title=title,
    )




def _build_standalone_resource_scenes(group: ContentGroup, scene_id: int) -> list[Scene]:
    resource = group.anchor
    captions = group.captions
    caption = captions[0].text if captions else ""
    dur = _duration(caption) if caption else MIN_DURATION
    target = f"{resource.text}|||{caption}" if caption else resource.text
    return [Scene(
        scene_id=scene_id,
        voiceover=caption,
        duration=dur,
        events=[
            TimelineEvent(time=0.0, action="SHOW_RESOURCE", target=target, duration=SPAWN_DURATION),
            TimelineEvent(time=round(dur - FADE_DURATION, 1), action="FADE", target="*", duration=FADE_DURATION),
        ],
    )]


# --- Dispatcher ---

def build_timeline(groups: list[ContentGroup], section_name: str = "section") -> list[Scene]:
    """Convert content groups into a flat list of scenes.

    section_name is used to derive per-scene narration paths for list scenes
    that need pre-narrated per-item WAVs (e.g., "section_1").
    """
    scenes: list[Scene] = []
    scene_id = 1

    for group in groups:
        if group.kind == "heading":
            new_scenes = _build_heading_scenes(group, scene_id)
        elif group.kind == "paragraph":
            if group.list_items:
                new_scenes = _build_paragraph_list_scenes(group, scene_id, section_name)
            elif group.resources:
                new_scenes = _build_paragraph_resource_scenes(group, scene_id, section_name)
            else:
                new_scenes = _build_paragraph_blank_scenes(group, scene_id, section_name)
        elif group.kind in ("image", "code_block", "table"):
            # Dangling resources (no paragraph context) are skipped — they produce
            # either silent holds or narrate-the-caption scenes that lack the
            # surrounding text needed to understand what's shown. The paragraph-
            # anchored scenes carry the educational content.
            logger.info(f"Skipping dangling {group.kind} group: {group.anchor.text[:60]}")
            continue
        elif group.kind == "list":
            new_scenes = _build_standalone_list_scenes(group, scene_id, section_name)
        else:
            continue

        scenes.extend(new_scenes)
        scene_id = scenes[-1].scene_id + 1 if scenes else scene_id

    return scenes


# --- Serialization ---

def serialize_scene(scene: Scene) -> str:
    """Render a Scene to the timeline text format consumed by compiler and narrator."""
    lines = [
        f"SCENE {scene.scene_id}",
        f"TOTAL_DURATION: {scene.duration}s",
        f"VOICEOVER: {scene.voiceover}",
        "",
        "TIMELINE:",
    ]
    for ev in scene.events:
        # ``LEVEL n`` suffix encodes the nesting depth for SHOW_LIST_ITEM
        # events. We only emit it for non-zero levels so default flat lists
        # stay byte-identical to the old format.
        level_suffix = f" LEVEL {ev.level}" if getattr(ev, "level", 0) else ""
        if ev.target:
            lines.append(f'  {ev.time}s {ev.action} "{ev.target}" ({ev.duration}s){level_suffix}')
        else:
            lines.append(f"  {ev.time}s {ev.action} ({ev.duration}s){level_suffix}")
    return "\n".join(lines)


# --- File I/O ---

def _has_timelines(section_name: str) -> bool:
    return bool(list(TIMELINES_DIR.glob(f"timeline_{section_name}_scene_*.txt")))


def timeline_section_file(content_groups_file: Path) -> list[Path]:
    """Read content groups, build timeline, write scene files. Returns written paths."""
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    section_name = content_groups_file.stem
    content = content_groups_file.read_text(encoding="utf-8", errors="replace")
    groups = deserialize_groups(content)
    scenes = build_timeline(groups, section_name=section_name)

    written: list[Path] = []
    for scene in scenes:
        filename = f"timeline_{section_name}_scene_{scene.scene_id}.txt"
        out = TIMELINES_DIR / filename
        if out.exists():
            continue
        out.write_text(serialize_scene(scene), encoding="utf-8")
        written.append(out)

    return written


@timer(label="Timeline section")
def process_section(content_groups_file: Path) -> list[Path]:
    """Generate timeline scenes for a single section."""
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    section_name = content_groups_file.stem
    if _has_timelines(section_name):
        logger.info(f"Already has timelines: {section_name}")
        return []

    logger.info(f"Generating timelines for {content_groups_file.name}")
    written = timeline_section_file(content_groups_file)
    logger.info(f"Wrote {len(written)} timeline files for {section_name}")
    return written


# --- Watchdog ---

def start_watcher(executor=None) -> "Observer":
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    from concurrent.futures import ThreadPoolExecutor

    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    _executor = executor or ThreadPoolExecutor()

    class ContentGroupHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            filepath = Path(event.src_path)
            if filepath.suffix == ".txt" and filepath.stem.startswith("section_"):
                if not _has_timelines(filepath.stem):
                    logger.info(f"[watchdog] New content groups detected: {filepath.name}")
                    _executor.submit(_safe_process, filepath)

    def _safe_process(filepath: Path):
        try:
            process_section(filepath)
        except Exception as e:
            logger.error(f"Timeline generation failed: {filepath.name} — {e}")

    handler = ContentGroupHandler()
    observer = Observer()
    observer.schedule(handler, str(CONTENT_GROUPS_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer) -> None:
    observer.stop()
    observer.join()


# --- Batch processing ---

def process_all(executor: "ThreadPoolExecutor") -> None:
    """Process all pending sections concurrently using the shared executor."""
    from concurrent.futures import as_completed

    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    pending = [
        f for f in sorted(CONTENT_GROUPS_DIR.glob("section_*.txt"))
        if not _has_timelines(f.stem)
    ]
    if not pending:
        logger.info("All sections already have timelines. Nothing to do.")
        return

    logger.info(f"Found {len(pending)} pending sections. Processing concurrently...")

    futures = {executor.submit(process_section, f): f for f in pending}
    succeeded = failed = 0
    for future in as_completed(futures):
        group_file = futures[future]
        try:
            future.result()
            succeeded += 1
            logger.info(f"[{succeeded + failed}/{len(pending)}] Done: {group_file.name}")
        except Exception as e:
            failed += 1
            logger.error(f"[{succeeded + failed}/{len(pending)}] FAILED: {group_file.name} — {e}")

    logger.info(f"Completed: {succeeded} succeeded, {failed} failed out of {len(pending)}")


# --- CLI ---

def _print_scenes(scenes: list[Scene]) -> None:
    for scene in scenes:
        print(serialize_scene(scene))
        print()


if __name__ == "__main__":
    import sys
    import time
    import argparse
    from concurrent.futures import ThreadPoolExecutor

    from src.config.constants import PIPELINE_THREAD_WORKERS

    parser = argparse.ArgumentParser(description="LLM-based timeline generator")
    parser.add_argument("content_groups_file", type=str, nargs="?", help="Path to a content groups file")
    parser.add_argument("--all", action="store_true", help="Process all pending (concurrent)")
    parser.add_argument("--watch", action="store_true", help="Watch content groups dir for new files")
    args = parser.parse_args()

    _executor = ThreadPoolExecutor(max_workers=PIPELINE_THREAD_WORKERS)

    if args.watch:
        logger.info(f"Watching {CONTENT_GROUPS_DIR} for new content groups...")
        observer = start_watcher(executor=_executor)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_watcher(observer)
            _executor.shutdown(wait=True)
            logger.info("Stopped.")
    elif args.all:
        process_all(executor=_executor)
        _executor.shutdown(wait=True)
    elif args.content_groups_file:
        path = Path(args.content_groups_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        content = path.read_text(encoding="utf-8", errors="replace")
        groups = deserialize_groups(content)
        scenes = build_timeline(groups)
        _print_scenes(scenes)
    else:
        parser.print_help()
