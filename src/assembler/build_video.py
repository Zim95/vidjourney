"""
Build "Part" videos by packing consecutive section mp4s into ≥10-minute groups.

Per-section ``*_raster.mp4`` files in ``pipeline/scroll/output/`` are the
intermediate artifact; publishing-shaped output is a smaller set of "Part"
mp4s — each roughly 10 minutes or longer, each named with the book title +
part number + an LLM-generated summary of what's included.

Naming pattern:
    <Book Title> - Part <n> - <Summary>.mp4

The summary is a 3-6 word noun phrase derived from the section HEADINGs in
the part (one LLM call per part).

Sections are hard-concatenated (stream copy via ffmpeg's concat demuxer)
so the next section's narrator never starts before the previous one's
finishes. The crossfade transitions we tried first overlapped narration
audibly — clean cuts read better as audiobook chapters.

Idempotent: existing part files are re-used unless one of the input section
mp4s is newer than the part OR the existing part is unplayable (e.g. a
half-written file with no moov atom).

Usage:
    python -m src.assembler.build_video               # build all parts
    python -m src.assembler.build_video --dry-run     # show packing without writing
"""
import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

import requests

from src.utils import logger, timer
from src.scheduler import subprocess_slot
from src.config.constants import (
    GROUPING_SECTIONS_DIR,
    GROUPING_BOOK_TITLE,
    GROUPING_PART_MIN_DURATION_MINUTES,
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_MAX_RETRIES,
)


# Scroll pipeline writes per-section mp4s here. Filename pattern
# ``section_N_raster.mp4``. The "_raster" suffix marks the
# pre-rasterize+ffmpeg-pan build path (vs. the manim-animation build).
SCROLL_OUTPUT_DIR = Path("pipeline/scroll/output")
PARTS_DIR = Path("pipeline/scroll/parts")
SECTIONS_DIR = GROUPING_SECTIONS_DIR
BOOK_TITLE = GROUPING_BOOK_TITLE
MIN_PART_DURATION_S = GROUPING_PART_MIN_DURATION_MINUTES * 60.0


# --- File discovery & duration ---

def _is_valid_mp4(path: Path) -> bool:
    """Return True iff ffprobe reads the file and pix_fmt is yuv420p.

    Filters out two failure modes: half-written files (no moov atom) from
    crashed/killed builds, and yuv444p stragglers from before the pix_fmt
    fix landed. Both would render parts unplayable in QuickTime/browsers.
    """
    try:
        with subprocess_slot():
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, check=True,
            )
        return r.stdout.strip() == "yuv420p"
    except subprocess.CalledProcessError:
        return False


def _list_section_videos() -> list[tuple[int, Path]]:
    """Find all VALID per-section scroll mp4s and return them sorted by
    section id. Invalid mp4s (mid-write, yuv444p) are silently skipped so
    parts only contain playable content; they'll get included on a later
    run once their builds finish."""
    sections: list[tuple[int, Path]] = []
    for p in SCROLL_OUTPUT_DIR.glob("section_*_raster.mp4"):
        m = re.match(r"section_(\d+)_raster\.mp4", p.name)
        if m and _is_valid_mp4(p):
            sections.append((int(m.group(1)), p))
    sections.sort(key=lambda t: t[0])
    return sections


def _video_duration_seconds(path: Path) -> float:
    try:
        with subprocess_slot():
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True, text=True, check=True,
            )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as exc:
        logger.warning(f"ffprobe failed for {path.name}: {exc}")
        return 0.0


def _section_heading(section_id: int) -> str:
    """Pull the section's HEADING line from the ingested source file."""
    src = SECTIONS_DIR / f"section_{section_id}.txt"
    if not src.exists():
        return ""
    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("HEADING "):
            return line[len("HEADING "):].strip()
    return ""


# --- Bin packing ---

def _bin_pack(
    sections: list[tuple[int, Path]],
    min_dur_seconds: float,
) -> list[list[tuple[int, Path, float]]]:
    """Greedy pack: accumulate sections in order until cumulative duration
    reaches ``min_dur_seconds``, then start a new part.

    Stops entirely on the FIRST section-id gap. If section N is invalid /
    missing, no parts are emitted past section N-1 even if there are valid
    sections beyond — the assumption is the listener should hear a clean,
    contiguous prefix of the book rather than a partial later chapter with
    holes. Once every section is rebuilt and the gap closes, a subsequent
    run picks up the rest automatically.

    Each part entry is ``(section_id, mp4_path, duration_seconds)``.
    """
    parts: list[list[tuple[int, Path, float]]] = []
    current: list[tuple[int, Path, float]] = []
    current_dur = 0.0
    prev_sid: int | None = None

    for sid, path in sections:
        if prev_sid is not None and sid != prev_sid + 1:
            # Gap detected — stop bin-packing entirely. Emit whatever we
            # have so far (orphan tail merges into the previous part if it
            # didn't hit the minimum, so we don't ship a stub).
            break
        dur = _video_duration_seconds(path)
        current.append((sid, path, dur))
        current_dur += dur
        if current_dur >= min_dur_seconds:
            parts.append(current)
            current = []
            current_dur = 0.0
        prev_sid = sid

    if current:
        if parts:
            parts[-1].extend(current)
        else:
            parts.append(current)

    return parts


# --- LLM-generated part title ---

PART_TITLE_PROMPT = """\
You are naming a "Part" of a book-to-video adaptation. Given the section
headings that appear in this part (in order), produce a 3-6 word noun-phrase
summary title that captures the main theme.

Section headings in this part (in order):
{headings}

Reply with valid JSON only:
{{"title": "<3-6 word noun phrase, no trailing punctuation>"}}

Examples:
- ["Reliability", "Hardware faults", "Software errors", "Human errors"]
  → {{"title": "Reliability in data systems"}}
- ["Designing for scale", "Describing load", "Describing performance"]
  → {{"title": "Scaling data systems"}}
- ["The data model", "Document model", "Graph model"]
  → {{"title": "Choosing a data model"}}

Rules:
- 3-6 words total. NO punctuation, NO trailing period.
- Use the actual subject matter from the headings; don't invent topics.
- If headings are too vague to summarize, default to "Continuing the journey"."""


def _llm_part_title(headings: list[str]) -> str:
    cleaned = [h.strip() for h in headings if h and h.strip()]
    if not cleaned:
        return "Continuing the journey"

    prompt = PART_TITLE_PROMPT.format(headings=json.dumps(cleaned, indent=2))

    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        try:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": OLLAMA_CHAT_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "format": "json",
                    "options": {"num_ctx": 8192, "temperature": 0},
                },
                timeout=120,
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
            data = json.loads(content)
            title = (data.get("title") or "").strip().rstrip(".,;:!?").strip()
            if title:
                return title
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(f"Part title attempt {attempt}/{OLLAMA_MAX_RETRIES} failed: {exc}")

    return "Continuing the journey"


# --- File naming & concat ---

def _safe_filename(name: str) -> str:
    name = re.sub(r"[<>:\"/\\|?*]", "-", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _is_part_stale(output_file: Path, part: list[tuple[int, Path, float]]) -> bool:
    """Stale if any input section is newer than the output OR if the output
    itself is unreadable (half-written mp4 left behind by a killed ffmpeg
    job — its moov atom never got finalized). The validity check stops the
    mtime-only logic from skipping over a 27-MB corrupted file that the
    user can't actually play."""
    if not _is_valid_mp4(output_file):
        return True
    output_mtime = output_file.stat().st_mtime
    return any(path.stat().st_mtime > output_mtime for _sid, path, _dur in part)


def _concat_videos(
    part: list[tuple[int, Path, float]],
    output_file: Path,
) -> Path:
    """Hard-concatenate the part's section mp4s in order into a single
    mp4 — no crossfade, no overlap. Section N's narrator finishes
    completely before section N+1 starts.

    Uses ffmpeg's concat demuxer with stream copy (``-c copy``) so no
    re-encode happens; the section mp4s are emitted from the scroll
    pipeline with identical codec/resolution/framerate parameters which
    is the precondition the concat demuxer requires.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists() and not _is_part_stale(output_file, part):
        return output_file

    paths = [p for _sid, p, _d in part]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        list_file = Path(f.name)
        for p in paths:
            f.write(f"file '{p.resolve()}'\n")
    try:
        with subprocess_slot():
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(list_file), "-c", "copy", str(output_file)],
                check=True, capture_output=True,
            )
    finally:
        list_file.unlink(missing_ok=True)
    return output_file


# --- Public entry ---

@timer(label="Build all parts")
def build_all_parts(dry_run: bool = False) -> list[Path]:
    """Pack section mp4s into ≥10-min parts, name via LLM, write to PARTS_DIR.

    Idempotent: existing part files are re-used unless any input section
    mp4 is newer than the part. ``dry_run=True`` reports the packing +
    filenames without invoking ffmpeg.
    """
    sections = _list_section_videos()
    if not sections:
        logger.warning(f"No section mp4s found in {SCROLL_OUTPUT_DIR}")
        return []

    logger.info(f"Found {len(sections)} section mp4s (section_{sections[0][0]} … section_{sections[-1][0]})")
    parts = _bin_pack(sections, MIN_PART_DURATION_S)
    logger.info(
        f"Packed into {len(parts)} parts "
        f"(target ≥{GROUPING_PART_MIN_DURATION_MINUTES:.1f} min each, hard-cut concat)"
    )

    written: list[Path] = []
    for i, part in enumerate(parts, 1):
        section_ids = [sid for sid, _, _ in part]
        total_dur_min = sum(d for _, _, d in part) / 60.0

        headings = [_section_heading(sid) for sid in section_ids]
        summary = _llm_part_title(headings) if not dry_run else "(title)"

        filename = _safe_filename(f"{BOOK_TITLE} - Part {i} - {summary}.mp4")
        output_file = PARTS_DIR / filename

        logger.info(
            f"  Part {i}: sections {section_ids[0]}-{section_ids[-1]} "
            f"({len(part)} sections, {total_dur_min:.1f} min) → {filename}"
        )
        if dry_run:
            written.append(output_file)
            continue

        if output_file.exists() and not _is_part_stale(output_file, part):
            logger.info("    already exists, skipping")
        else:
            if output_file.exists():
                logger.info("    inputs newer than part — rebuilding")
                output_file.unlink()
            _concat_videos(part, output_file)
            logger.info(f"    wrote {output_file.name}")
        written.append(output_file)

    return written


def _assemble_watcher(sched, args):
    """Repack all parts whenever a new section mp4 lands (build is idempotent)."""
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    SCROLL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def _rebuild():
        try:
            build_all_parts(dry_run=False)
        except Exception as exc:
            logger.error(f"[assemble] repack failed: {exc}")

    class _Handler(FileSystemEventHandler):
        def on_created(self, event):
            p = Path(event.src_path)
            if not event.is_directory and p.name.endswith("_raster.mp4"):
                logger.info(f"[assemble] new section mp4: {p.name} → repack")
                sched.io.submit(_rebuild)

    obs = Observer()
    obs.schedule(_Handler(), str(SCROLL_OUTPUT_DIR), recursive=False)
    obs.start()
    return obs


from src.stage_cli import Stage, run_stage

STAGE = Stage(
    name="assembler.build_video",
    run_all_fn=lambda sched, args: build_all_parts(dry_run=args.dry_run),
    start_watcher_fn=_assemble_watcher,
    watch_dir=SCROLL_OUTPUT_DIR,
    extra_args=[("--dry-run", {"action": "store_true", "help": "show packing plan without writing"})],
    supports_item=False,
    pool="io",
)


if __name__ == "__main__":
    # Build all parts:  --all   (preview: --all --dry-run;  cascade: --watch)
    run_stage(STAGE)
    if args.dry_run:
        print(f"\nDry run — would write {len(paths)} part files:")
        for p in paths:
            print(f"  {p}")
    else:
        print(f"\nWrote {len(paths)} part files to {PARTS_DIR}")
