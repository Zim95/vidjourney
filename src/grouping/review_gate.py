"""
Interactive terminal review gate — the one human step in the pipeline.

Sits between grouping and render: reads ``pipeline/groups/content_groups/
section_N.txt`` and writes ``pipeline/groups/approved/section_N.txt`` (which
render consumes). Its whole job is letting a human correct detector mistakes on
the handful of sections that need it, while everything else auto-passes.

Terminal contract::

    python -m src.grouping.review_gate --all           # auto-pass all pending (no questions)
    python -m src.grouping.review_gate --approve-clean  # alias for --all
    python -m src.grouping.review_gate --watch          # cascade: auto-pass sections as they land
    python -m src.grouping.review_gate <section.txt>    # interactively review ONE section
    python -m src.grouping.review_gate --review [--all] # interactively review pending (or one) section(s)

Two things it reviews interactively:

- **Ambiguous quotes** — a paragraph that ends in a bare-name / non-year
  em-dash attribution (``has_quote_attribution`` fires but the strict
  ``is_quote`` does not). Confirming promotes the group to ``kind=quote`` so
  render shows it as a quote; declining keeps it prose. Render trusts these
  explicit kinds (``build_raster`` passes ``trust_kinds=True``), so the gate is
  authoritative for ambiguous quotes.
- **Borderline code lines** — code-block lines whose model probability sits
  within ``ML_CODE_LINE_CONFIDENCE_MARGIN`` of the threshold. A correction is
  appended to ``src/ingestion/ml/training_code_snippets/`` (the training-data
  flywheel), so the *next* ``train.py`` + re-ingest improves the detector.
  (The current section's rendered code image is not re-rendered live — that
  lands on the next ingest.)

Batch modes (``--all`` / ``--watch`` / ``--approve-clean``) never prompt and
never block, so the automated cascade always drains; the human overlaps it by
running interactive review on whichever sections they choose.

Table re-tuning is not yet wired here (follow-up).
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from src.utils import logger
from src.grouping.llm_grouper import (
    ContentGroup,
    Element,
    deserialize_groups,
    serialize_groups,
)
from src.ingestion.quote_attribution import (
    is_quote,
    has_quote_attribution,
    split_quote_body_and_attribution,
)
from src.config.constants import (
    GROUPING_CONTENT_GROUPS_DIR,
    GROUPING_APPROVED_DIR,
    ML_CODE_LINE_THRESHOLD,
    ML_CODE_LINE_CONFIDENCE_MARGIN,
    ML_TRAINING_DATA_DIR,
)

CONTENT_GROUPS_DIR = GROUPING_CONTENT_GROUPS_DIR
APPROVED_DIR = GROUPING_APPROVED_DIR


# --------------------------------------------------------------------------- #
# Auto-pass (batch / cascade)
# --------------------------------------------------------------------------- #
def auto_pass(cg_path: Path) -> Path:
    """Copy a section's content groups straight to approved/ (no review)."""
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    out = APPROVED_DIR / cg_path.name
    shutil.copyfile(cg_path, out)
    return out


def pending() -> list[Path]:
    """content_groups sections without a corresponding approved/ file."""
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    return [
        f
        for f in sorted(CONTENT_GROUPS_DIR.glob("section_*.txt"))
        if not (APPROVED_DIR / f.name).exists()
    ]


def approve_clean() -> None:
    """Auto-pass every pending section — the batch, no-questions path."""
    todo = pending()
    for f in todo:
        auto_pass(f)
    logger.info(f"[gate] auto-passed {len(todo)} section(s) → {APPROVED_DIR}")


# --------------------------------------------------------------------------- #
# Flag detection (interactive path only)
# --------------------------------------------------------------------------- #
def _quote_flags(groups: list[ContentGroup]) -> list[int]:
    """Indices of paragraph groups with an ambiguous em-dash attribution."""
    out: list[int] = []
    for gi, g in enumerate(groups):
        if g.kind == "paragraph" and not g.list_items and not g.resources:
            text = g.anchor.text.strip()
            if has_quote_attribution(text) and not is_quote(text):
                out.append(gi)
    return out


def _code_source_txts(groups: list[ContentGroup]) -> list[Path]:
    """Source code-block .txt files referenced (as rendered PNGs) by a section."""
    txts: list[Path] = []
    seen: set[str] = set()
    for g in groups:
        for el in g.elements:
            if el.kind == "IMAGE" and "code_block_images" in el.text:
                p = Path(
                    el.text.replace("code_block_images", "code_blocks").replace(".png", ".txt")
                )
                if p.exists() and p.as_posix() not in seen:
                    seen.add(p.as_posix())
                    txts.append(p)
    return txts


def _code_flags(groups: list[ContentGroup]) -> list[tuple[Path, str, float]]:
    """Borderline code lines: (source_txt, line, proba). Requires the model +
    embeddings; degrades to [] if unavailable."""
    txts = _code_source_txts(groups)
    if not txts:
        return []
    try:
        from src.ingestion.ml.train import predict_is_code_proba_batch
    except Exception:
        return []
    flags: list[tuple[Path, str, float]] = []
    for tp in txts:
        lines = [
            l.strip()
            for l in tp.read_text(encoding="utf-8", errors="replace").splitlines()
            if l.strip()
        ]
        if not lines:
            continue
        try:
            probas = predict_is_code_proba_batch(lines)
        except Exception as exc:
            logger.warning(f"[gate] code proba failed for {tp.name}: {exc}")
            continue
        for line, p in zip(lines, probas):
            if abs(p - ML_CODE_LINE_THRESHOLD) <= ML_CODE_LINE_CONFIDENCE_MARGIN:
                flags.append((tp, line, p))
    return flags


# --------------------------------------------------------------------------- #
# Flywheel
# --------------------------------------------------------------------------- #
def _append_training(labels: list[tuple[str, str]], section_name: str) -> None:
    """Append ``(text, "code"|"text")`` corrections in the schema
    ``build_code_training_data`` globs, so the next train.py picks them up."""
    if not labels:
        return
    ML_TRAINING_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = ML_TRAINING_DATA_DIR / f"gate_{section_name}.json"
    out.write_text(
        json.dumps([{"text": t, "label": lab} for t, lab in labels], indent=2),
        encoding="utf-8",
    )
    logger.info(f"[gate] wrote {len(labels)} labelled line(s) → {out.name} (retrain to apply)")


# --------------------------------------------------------------------------- #
# Interactive review
# --------------------------------------------------------------------------- #
def _ask(prompt: str, choices: list[str], default: str) -> str:
    try:
        while True:
            r = input(prompt).strip().lower()
            if not r:
                return default
            if r in choices:
                return r
            print(f"  (choose one of: {', '.join(choices)})")
    except EOFError:
        return default


def _write_approved(groups: list[ContentGroup], section_name: str) -> Path:
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    out = APPROVED_DIR / f"{section_name}.txt"
    out.write_text(serialize_groups(groups), encoding="utf-8")
    return out


def review_section(cg_path: Path) -> Path:
    """Interactively review one section and write approved/section_N.txt."""
    section_name = cg_path.stem
    groups = deserialize_groups(cg_path.read_text(encoding="utf-8"))

    qflags = _quote_flags(groups)
    cflags = _code_flags(groups)
    total = len(qflags) + len(cflags)

    if total == 0:
        print(f"✓ {section_name}: no flags — auto-approving.")
        out = _write_approved(groups, section_name)
        print(f"  approved → {out}")
        return out

    print(f"\n── Review: {section_name}  ({total} flag{'s' if total != 1 else ''}) ──")
    n = 0

    for gi in qflags:
        n += 1
        text = groups[gi].anchor.text.strip()
        body, attribution = split_quote_body_and_attribution(text)
        print(f"\n[{n}/{total}] QUOTE?  ambiguous em-dash attribution")
        print(f'  "{text[:200]}"')
        print(f"     body:        {body!r}")
        print(f"     attribution: {attribution!r}")
        ans = _ask("  (q)uote / (p)rose / (s)kip ? ", ["q", "p", "s"], "s")
        if ans == "q":
            groups[gi] = ContentGroup(kind="quote", elements=[Element(kind="QUOTE", text=text)])
            print("  ✓ tagged QUOTE")
        elif ans == "p":
            print("  ✓ kept as prose")

    labels: list[tuple[str, str]] = []
    for tp, line, proba in cflags:
        n += 1
        print(f"\n[{n}/{total}] CODE?  {tp.name}  proba={proba:.2f} (threshold {ML_CODE_LINE_THRESHOLD})")
        print(f"     {line[:200]}")
        ans = _ask("  (c)ode / (t)ext / (s)kip ? ", ["c", "t", "s"], "s")
        if ans == "c":
            labels.append((line, "code"))
        elif ans == "t":
            labels.append((line, "text"))
    _append_training(labels, section_name)

    out = _write_approved(groups, section_name)
    print(f"\n{section_name} approved → {out}")
    return out


# --------------------------------------------------------------------------- #
# Watch (cascade auto-pass)
# --------------------------------------------------------------------------- #
def start_watcher():
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    CONTENT_GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)

    class _Handler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            p = Path(event.src_path)
            if p.suffix == ".txt" and p.stem.startswith("section_"):
                if not (APPROVED_DIR / p.name).exists():
                    logger.info(f"[gate] [watch] auto-pass {p.name}")
                    try:
                        auto_pass(p)
                    except Exception as exc:
                        logger.error(f"[gate] auto-pass failed for {p.name}: {exc}")

    obs = Observer()
    obs.schedule(_Handler(), str(CONTENT_GROUPS_DIR), recursive=False)
    obs.start()
    return obs


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Interactive review gate")
    ap.add_argument("section_file", nargs="?", help="a single content_groups section to review")
    ap.add_argument("--all", action="store_true", help="auto-pass all pending sections (no questions)")
    ap.add_argument("--approve-clean", dest="approve_clean", action="store_true", help="alias for --all")
    ap.add_argument("--watch", action="store_true", help="watch content_groups and auto-pass new sections")
    ap.add_argument("--review", action="store_true", help="interactively review pending (or the given) section(s)")
    args = ap.parse_args(argv)

    if args.watch:
        logger.info(f"[gate] watching {CONTENT_GROUPS_DIR} (auto-pass) ...")
        obs = start_watcher()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            obs.stop()
            obs.join()
            logger.info("[gate] stopped.")
    elif args.review:
        targets = [Path(args.section_file)] if args.section_file else pending()
        if not targets:
            print("No pending sections to review.")
            return
        for t in targets:
            review_section(t)
    elif args.all or args.approve_clean:
        approve_clean()
    elif args.section_file:
        review_section(Path(args.section_file))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
