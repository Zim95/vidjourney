"""Generate SEO-optimized YouTube titles + descriptions + tags for every part.

Writes one ``pipeline/descriptions/part_NN.md`` per part. The file is
paste-ready: each section (Title / Description / Tags) is delimited by a
markdown H2 so the operator can copy the contents of one section without
including the others.

The packing is read from the canonical bin packer in
``src.assembler.build_video`` so the section list per part stays in sync
with whatever ``pipeline/scroll/parts/`` was last assembled with.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from src.assembler.build_video import (
    _list_section_videos,
    _bin_pack,
    _section_heading,
    MIN_PART_DURATION_S,
)

PARTS_DIR = Path("pipeline/scroll/parts")
OUT_DIR = Path("pipeline/descriptions")

BOOK_TITLE = "Designing Data-Intensive Applications"

# Keyword → hashtag/tag mapping. Order matters only for readability; we
# de-dupe at the end. The check is substring against the joined lowercase
# section headings, so plurals/inflections are handled implicitly when the
# stem matches.
TOPIC_MAP: list[tuple[str, list[str]]] = [
    ("reliab",          ["Reliability", "FaultTolerance"]),
    ("fault",           ["FaultTolerance"]),
    ("chaos",           ["ChaosEngineering"]),
    ("scalab",          ["Scalability"]),
    ("percentile",      ["LatencyPercentiles", "TailLatency", "PerformanceEngineering"]),
    ("performance",     ["PerformanceEngineering"]),
    ("maintainab",      ["Maintainability", "TechnicalDebt"]),
    ("operab",          ["DevOps", "SRE"]),
    ("simplicity",      ["SoftwareArchitecture"]),
    ("evolv",           ["SoftwareEvolution"]),
    ("data model",      ["DataModeling"]),
    ("relational",      ["SQL", "RelationalDatabases", "PostgreSQL"]),
    ("document",        ["DocumentDatabases", "NoSQL", "MongoDB"]),
    ("nosql",           ["NoSQL"]),
    ("graph",           ["GraphDatabases", "Neo4j", "Cypher"]),
    ("query language",  ["QueryLanguages"]),
    ("sparql",          ["SPARQL", "SemanticWeb"]),
    ("storage",         ["StorageEngines"]),
    ("retrieval",       ["StorageEngines"]),
    ("b-tree",          ["BTree", "IndexingStructures"]),
    ("lsm",             ["LSMTree", "IndexingStructures"]),
    ("index",           ["IndexingStructures"]),
    ("column",          ["ColumnarStorage", "DataWarehouse"]),
    ("warehouse",       ["DataWarehouse", "OLAP"]),
    ("encoding",        ["DataSerialization", "Protobuf", "Avro"]),
    ("serializ",        ["DataSerialization"]),
    ("avro",            ["Avro", "DataSerialization"]),
    ("thrift",          ["Thrift", "DataSerialization"]),
    ("protocol buffer", ["Protobuf", "DataSerialization"]),
    ("dataflow",        ["Dataflow"]),
    ("replication",     ["Replication"]),
    ("leader",          ["LeaderElection"]),
    ("multi-leader",    ["MultiLeaderReplication"]),
    ("leaderless",      ["LeaderlessReplication"]),
    ("partition",       ["Partitioning", "Sharding"]),
    ("sharding",        ["Sharding"]),
    ("rebalanc",        ["Rebalancing"]),
    ("transaction",     ["Transactions", "ACID"]),
    ("acid",            ["ACID"]),
    ("isolation",       ["TransactionIsolation"]),
    ("serializab",      ["Serializability"]),
    ("snapshot",        ["SnapshotIsolation", "MVCC"]),
    ("concurrency",     ["ConcurrencyControl"]),
    ("write skew",      ["WriteSkew"]),
    ("lost update",     ["LostUpdates"]),
    ("phantom",         ["PhantomReads"]),
    ("unreliable",      ["DistributedSystemsFailures"]),
    ("network",         ["DistributedSystems", "NetworkPartitions"]),
    ("clock",           ["ClockSynchronization", "TimingInDistributedSystems"]),
    ("byzantine",       ["ByzantineFaults"]),
    ("knowledge",       ["DistributedSystems"]),
    ("consistency",     ["Consistency"]),
    ("consensus",       ["Consensus", "Paxos", "Raft"]),
    ("linearizab",      ["Linearizability"]),
    ("ordering",        ["Ordering", "CausalConsistency"]),
    ("causal",          ["CausalConsistency"]),
    ("total order",     ["TotalOrderBroadcast"]),
    ("two-phase",       ["TwoPhaseCommit"]),
    ("commit",          ["DistributedCommit"]),
    ("zookeeper",       ["ZooKeeper", "Coordination"]),
    ("membership",      ["Coordination"]),
    ("batch",           ["BatchProcessing", "MapReduce", "BigData"]),
    ("mapreduce",       ["MapReduce", "Hadoop"]),
    ("hadoop",          ["Hadoop", "HDFS"]),
    ("hdfs",            ["HDFS"]),
    ("join",            ["DataJoins"]),
    ("workflow",        ["DataPipelines"]),
    ("stream",          ["StreamProcessing", "EventDriven"]),
    ("kafka",           ["Kafka", "MessageBrokers"]),
    ("event",           ["EventDriven", "EventSourcing"]),
    ("change data capture", ["CDC", "ChangeDataCapture"]),
    ("log",             ["EventLogs"]),
    ("derived",         ["DerivedData", "MaterializedViews"]),
    ("future",          ["DataSystemsArchitecture"]),
    ("end-to-end",      ["EndToEndArgument"]),
    ("integrity",       ["DataIntegrity"]),
    ("ethics",          ["DataEthics", "Privacy"]),
    ("privacy",         ["Privacy"]),
    ("foundations",     ["SystemDesignFundamentals"]),
]

BASE_HASHTAGS = [
    "DDIA",
    "SystemDesign",
    "DistributedSystems",
    "BackendEngineering",
    "SystemDesignInterview",
    "SoftwareArchitecture",
    "MartinKleppmann",
    "BookSummary",
]

BASE_TAGS_FLAT = [
    "DDIA",
    "designing data-intensive applications",
    "system design",
    "distributed systems",
    "system design interview",
    "backend engineering",
    "software architecture",
    "martin kleppmann",
    "book summary",
]

CTA_BASE = (
    "🔔 Subscribe for the full DDIA series — each video walks one chapter "
    "of Designing Data-Intensive Applications, the canonical reference for "
    "anyone building backend, distributed, or data-intensive systems."
)

SOURCE_LINE = (
    "📖 Source: *Designing Data-Intensive Applications* by Martin Kleppmann "
    "(O'Reilly, 2017)"
)


def fmt_time(seconds: float) -> str:
    total = int(round(seconds))
    mm, ss = divmod(total, 60)
    return f"{mm:02d}:{ss:02d}"


def detect_topic_tags(headings: list[str]) -> list[str]:
    text = " ".join(headings).lower()
    out: list[str] = []
    for keyword, tags in TOPIC_MAP:
        if keyword in text:
            for t in tags:
                if t not in out:
                    out.append(t)
    return out


def find_part_mp4(part_num: int) -> Path | None:
    pattern = re.compile(
        rf"^{re.escape(BOOK_TITLE)} - Part {part_num} - (.+)\.mp4$"
    )
    for p in PARTS_DIR.glob("*.mp4"):
        if pattern.match(p.name):
            return p
    return None


def part_title_from_filename(part_num: int) -> tuple[str, Path | None]:
    """Return (LLM-generated title chunk, source mp4 path).

    The title chunk is the bit between ``Part N -`` and ``.mp4`` — that's
    what the build-video LLM produced for the part. We use it as the
    YouTube title's main descriptor.
    """
    mp4 = find_part_mp4(part_num)
    if not mp4:
        return ("Continuing the journey", None)
    m = re.match(
        rf"^{re.escape(BOOK_TITLE)} - Part {part_num} - (.+)\.mp4$",
        mp4.name,
    )
    return (m.group(1) if m else "Continuing the journey"), mp4


def render_part_md(
    part_num: int,
    section_list: list[tuple[int, Path, float]],
    llm_title: str,
    mp4: Path | None,
) -> str:
    section_ids = [sid for sid, _, _ in section_list]
    headings = [_section_heading(sid) or f"Section {sid}" for sid in section_ids]

    # Timestamps within this part — cumulative section durations.
    chapters: list[tuple[float, str]] = []
    t = 0.0
    for (sid, _path, dur), heading in zip(section_list, headings):
        chapters.append((t, heading))
        t += dur
    total_seconds = t

    # Hyphenated, matching the part mp4 filename stem (also what the uploader
    # uses by default): "<book> - Part N - <llm title>".
    youtube_title = f"{BOOK_TITLE} - Part {part_num} - {llm_title}"
    # YouTube title hard cap is 100 chars.
    if len(youtube_title) > 100:
        youtube_title = youtube_title[:99].rstrip() + "…"

    headings_sentence = ", ".join(headings)
    chapters_lines = "\n".join(
        f"{fmt_time(t)} — {h}" for t, h in chapters
    )
    bullets_lines = "\n".join(f"• {h}" for h in headings)

    topic_tags = detect_topic_tags(headings)
    hashtags = " ".join(f"#{t}" for t in BASE_HASHTAGS + topic_tags)

    tags_flat = BASE_TAGS_FLAT + [t.lower() for t in topic_tags] + [
        h.lower() for h in headings
    ]
    # De-dupe while preserving order; YouTube tags max ~500 chars total.
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tags_flat:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    youtube_tags = ", ".join(deduped)

    description = f"""Designing Data-Intensive Applications (DDIA) Part {part_num}: {llm_title}. \
Continuing Martin Kleppmann's classic system design book, chapter by chapter, \
for backend, distributed-systems, and data-engineering practitioners.

This part walks through {headings_sentence}. Each section is a tight, \
animated walkthrough of one heading from the book — built so you can use \
it as a refresher before a system design interview, an on-call rotation, \
or your next architecture review.

⏱ Chapters
{chapters_lines}

🎯 Topics covered
{bullets_lines}

💬 What part of this resonates with systems you've built or operated? \
Drop your example in the comments — the most useful thread on these \
videos is the one with real-world war stories.

{CTA_BASE}

{SOURCE_LINE}

{hashtags}"""

    section_lines = "\n".join(
        f"- section_{sid}: {h}" for sid, h in zip(section_ids, headings)
    )

    mp4_line = (
        f"`{mp4.as_posix()}`"
        if mp4 is not None
        else "_(no current build mp4 found)_"
    )

    return f"""# Part {part_num} — {llm_title}

Total runtime: {fmt_time(total_seconds)} · {len(section_list)} section{'s' if len(section_list) != 1 else ''}

## YouTube Title

{youtube_title}

## YouTube Description

{description}

## YouTube Tags

{youtube_tags}

## Sections covered

{section_lines}

## File

{mp4_line}
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sections = _list_section_videos()
    parts = _bin_pack(sections, MIN_PART_DURATION_S)

    index_lines: list[str] = [
        "# Part descriptions — index",
        "",
        f"Generated for {len(parts)} parts from the current `pipeline/scroll/parts/` build.",
        "Each `part_NN.md` is paste-ready: copy the Title, Description, and Tags sections directly into YouTube Studio.",
        "",
        "| Part | Runtime | Sections | Title |",
        "|------|---------|----------|-------|",
    ]

    for i, part in enumerate(parts, 1):
        llm_title, mp4 = part_title_from_filename(i)
        md = render_part_md(i, part, llm_title, mp4)
        out_path = OUT_DIR / f"part_{i:02d}.md"
        out_path.write_text(md, encoding="utf-8")

        total = sum(d for _, _, d in part)
        sids = [sid for sid, _, _ in part]
        if len(sids) == 1:
            sid_str = str(sids[0])
        else:
            sid_str = f"{sids[0]}–{sids[-1]}"
        index_lines.append(
            f"| {i} | {fmt_time(total)} | {sid_str} | {llm_title} |"
        )
        print(f"  part_{i:02d}.md ← {llm_title}")

    (OUT_DIR / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    print(f"\nWrote {len(parts)} description files + INDEX.md to {OUT_DIR}/")


if __name__ == "__main__":
    main()
