"""
LLM-based scene grouping backends (Gemini, Ollama).

Takes section files and sends them to an LLM for scene grouping.
"""
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from google import genai

from src.config.constants import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_MAX_TOKENS_PER_MINUTE,
    GEMINI_MAX_REQUESTS_PER_MINUTE,
    GEMINI_MAX_REQUESTS_PER_DAY,
    GEMINI_MAX_RETRIES,
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
    GROUPING_SECTIONS_DIR,
    GROUPING_SCENE_GROUPS_DIR,
    GROUPING_CHARS_PER_TOKEN,
)


PROMPT = """\
You are a scene grouper for an educational video tool.

You will receive the contents of a section from a textbook.
The section contains elements like HEADING, PARAGRAPH, CODE_BLOCK, IMAGE, TABLE, CAPTION, LIST_ITEM.

Your job is to group these elements into scenes. Each scene is a logical unit that will become one animation in a video.

Rules:
1. A scene groups together content that talks about the same idea.
2. If a PARAGRAPH is clearly describing a nearby IMAGE, TABLE, or CODE_BLOCK, they belong in the same scene. Use CAPTIONs as hints.
3. If a PARAGRAPH has no associated resource, extract the key entities (concepts, nouns that matter) and relationships between them.
4. LIST_ITEMs that follow a PARAGRAPH belong in the same scene as that paragraph. Include the list item text in the NARRATE field.
5. HEADINGs start a new scene only if the content after them shifts topic.
6. Keep scenes focused. One scene = one idea. Don't merge unrelated paragraphs.
7. Ignore page_number lines.
8. NARRATE must contain ALL the text from the section — every PARAGRAPH and LIST_ITEM must appear in exactly one scene's NARRATE. Do not drop or skip any text.

Output format (plain text, no JSON, no markdown):

---
SCENE <number>
NARRATE: <ALL text content for this scene — paragraphs AND list items combined>
DISPLAY: <resource path if IMAGE/TABLE/CODE_BLOCK is associated, otherwise leave blank>
ENTITIES: <comma separated key concepts, only if no DISPLAY>
RELATIONS: <entity -- verb --> entity, comma separated, only if no DISPLAY>
---

Only include ENTITIES and RELATIONS when there is no DISPLAY resource.
Only include meaningful entities — no pronouns, no filler words.

Here is the section:

{section_content}
"""

SECTIONS_DIR = GROUPING_SECTIONS_DIR
SCENE_GROUPS_DIR = GROUPING_SCENE_GROUPS_DIR


def _estimate_tokens(text: str) -> int:
    return len(text) // GROUPING_CHARS_PER_TOKEN


def _build_prompt(section_content: str) -> str:
    return PROMPT.format(section_content=section_content)


def output_dir() -> Path:
    return SCENE_GROUPS_DIR


def output_path(section_file: Path) -> Path:
    return output_dir() / section_file.name


def collect_pending_files() -> list[Path]:
    section_files = sorted(SECTIONS_DIR.glob("section_*.txt"))
    return [f for f in section_files if not output_path(f).exists()]


# --- Backends ---

def _group_with_gemini(section_content: str) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=_build_prompt(section_content),
    )
    return response.text


def _group_with_ollama(section_content: str) -> str:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [
                {"role": "user", "content": _build_prompt(section_content)},
            ],
            "stream": False,
            "options": {"num_ctx": 8192},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


BACKENDS = {
    "gemini": _group_with_gemini,
    "ollama": _group_with_ollama,
}


def group_section_file(section_file: Path, backend: str = "gemini") -> str:
    content = section_file.read_text(encoding="utf-8", errors="replace")
    group_fn = BACKENDS.get(backend)
    if group_fn is None:
        raise ValueError(f"Unknown backend: {backend}. Choose from: {list(BACKENDS.keys())}")
    return group_fn(content)


# --- Handlers ---

def _process_file(section_file: Path, backend: str) -> tuple[Path, str | None, str | None]:
    try:
        result = group_section_file(section_file, backend=backend)
        return (section_file, result, None)
    except Exception as e:
        return (section_file, None, str(e))


def _build_batches(files: list[Path]) -> list[list[Path]]:
    max_tokens = GEMINI_MAX_TOKENS_PER_MINUTE
    max_requests = GEMINI_MAX_REQUESTS_PER_MINUTE

    batches: list[list[Path]] = []
    current_batch: list[Path] = []
    current_tokens = 0

    for f in files:
        content = f.read_text(encoding="utf-8", errors="replace")
        tokens = _estimate_tokens(_build_prompt(content))

        if current_batch and (current_tokens + tokens > max_tokens or len(current_batch) >= max_requests):
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(f)
        current_tokens += tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def _gemini_handler(pending: list[Path], backend: str, ) -> None:
    max_retries = GEMINI_MAX_RETRIES
    queue = list(pending)
    attempt = 0

    while queue and attempt <= max_retries:
        if attempt > 0:
            print(f"\nRetry {attempt}/{max_retries}: {len(queue)} file(s) to retry...")

        failed: list[Path] = []
        batches = _build_batches(queue)
        daily_limit = GEMINI_MAX_REQUESTS_PER_DAY
        total_requests = sum(len(b) for b in batches)

        if total_requests > daily_limit:
            print(f"Warning: {total_requests} requests needed but daily limit is {daily_limit}.")
            print(f"Will process first {daily_limit} requests.")

        requests_sent = 0
        for batch_idx, batch in enumerate(batches):
            if requests_sent >= daily_limit:
                print(f"Daily limit of {daily_limit} requests reached. Stopping.")
                failed.extend(f for remaining_batch in batches[batch_idx:] for f in remaining_batch)
                break

            remaining = daily_limit - requests_sent
            batch = batch[:remaining]

            batch_tokens = sum(
                _estimate_tokens(_build_prompt(f.read_text(encoding="utf-8", errors="replace")))
                for f in batch
            )
            print(f"Batch {batch_idx + 1}/{len(batches)}: {len(batch)} files, ~{batch_tokens} tokens")

            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = {
                    executor.submit(_process_file, f, backend): f
                    for f in batch
                }
                for future in as_completed(futures):
                    section_file, result, error = future.result()
                    if error:
                        print(f"  FAILED: {section_file.name} — {error}")
                        failed.append(section_file)
                    else:
                        out = output_path(section_file)
                        out.write_text(result, encoding="utf-8")
                        print(f"  OK: {section_file.name}")


            requests_sent += len(batch)

            if batch_idx < len(batches) - 1 and requests_sent < daily_limit:
                print("Waiting 60s for rate limit...")
                time.sleep(60)

        queue = failed
        attempt += 1

    if queue:
        print(f"\n{len(queue)} file(s) failed after {max_retries} retries:")
        for f in queue:
            print(f"  - {f.name}")


def _ollama_handler(pending: list[Path], backend: str) -> None:
    max_retries = OLLAMA_MAX_RETRIES
    # Ollama processes one request at a time — threading just queues them
    # and wastes resources. Sequential is cleaner and avoids thread pile-up.
    queue = list(pending)
    attempt = 0

    while queue and attempt <= max_retries:
        if attempt > 0:
            print(f"\nRetry {attempt}/{max_retries}: {len(queue)} file(s) to retry...")

        failed: list[Path] = []
        total = len(queue)

        for i, f in enumerate(queue, 1):
            section_file, result, error = _process_file(f, backend)
            if error:
                print(f"  [{i}/{total}] FAILED: {section_file.name} — {error}")
                failed.append(section_file)
            else:
                out = output_path(section_file)
                out.write_text(result, encoding="utf-8")
                print(f"  [{i}/{total}] OK: {section_file.name}")

        queue = failed
        attempt += 1

    if queue:
        print(f"\n{len(queue)} file(s) failed after {max_retries} retries:")
        for f in queue:
            print(f"  - {f.name}")


HANDLERS = {
    "gemini": _gemini_handler,
    "ollama": _ollama_handler,
}
