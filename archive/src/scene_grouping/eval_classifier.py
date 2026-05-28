"""
Eval harness for ``classify_paragraph``.

The 8 hand-labeled "quote" paragraphs are the eight book quotations the
attribution-splitter isolates from the DDIA sections. The "concept" set is
hand-picked prose from sections 1-10 — explanation/argument paragraphs that
should NOT be classified as quotes. The "abstract" set is transitional /
opinionated content that has no visual.

Run:
    .venv/bin/python -m src.scene_grouping.eval_classifier

It runs every labeled example through ``classify_paragraph`` and prints:
- per-example: predicted vs expected (✓/✗)
- summary: overall accuracy + confusion matrix

This is the safety net we wanted before swapping the quote-detection path.
If a future prompt edit or model swap regresses accuracy, the run output
makes it visible immediately.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from src.scene_grouping.llm_classifier import classify_paragraph, VALID_TYPES


@dataclass(frozen=True)
class LabeledExample:
    label: str          # one of VALID_TYPES
    text: str
    note: str = ""      # short description shown in CLI output


# --- The 8 attribution-style quotes the splitter isolates from DDIA ---

QUOTE_EXAMPLES: list[LabeledExample] = [
    LabeledExample(
        label="quote",
        note="Alan Kay, section_2",
        text=(
            "The Internet was done so well that most people think of it as a natural "
            "resource like the Pacific Ocean, rather than something that was man-made. "
            "When was the last time a technology with a scale like that was so error-free? "
            "—Alan Kay, in interview with Dr Dobb's Journal (2012)"
        ),
    ),
    LabeledExample(
        label="quote",
        note="Wittgenstein, section_19",
        text=(
            "The limits of my language mean the limits of my world. "
            "—Ludwig Wittgenstein, Tractatus Logico-Philosophicus (1922)"
        ),
    ),
    LabeledExample(
        label="quote",
        note="Feynman, section_67",
        text=(
            "For a successful technology, reality must take precedence over public "
            "relations, for nature cannot be fooled. "
            "—Richard Feynman, Rogers Commission Report (1986)"
        ),
    ),
    LabeledExample(
        label="quote",
        note="Douglas Adams, section_71",
        text=(
            "The major difference between a thing that might go wrong and a thing "
            "that cannot possibly go wrong is that when a thing that cannot possibly "
            "go wrong goes wrong it usually turns out to be impossible to get at or "
            "repair. —Douglas Adams, Mostly Harmless (1992)"
        ),
    ),
    LabeledExample(
        label="quote",
        note="Grace Hopper, section_95",
        text=(
            "Clearly, we must break away from the sequential and not limit the "
            "computers. We must state definitions and provide for priorities and "
            "descriptions of data. We must state relationships, not procedures. "
            "—Grace Murray Hopper, Management and the Computer of the Future (1962)"
        ),
    ),
    LabeledExample(
        label="quote",
        note="Corbett et al., section_111",
        text=(
            "Some authors have claimed that general two-phase commit is too "
            "expensive to support, because of the performance or availability "
            "problems that it brings. We believe it is better to have application "
            "programmers deal with performance problems due to overuse of "
            "transactions as bottlenecks arise, rather than always coding around "
            "the lack of transactions. "
            "—James Corbett et al., Spanner: Google's Globally-Distributed Database (2012)"
        ),
    ),
    LabeledExample(
        label="quote",
        note="Jay Kreps, section_150",
        text=(
            "Is it better to be alive and wrong or right and dead? "
            "—Jay Kreps, A Few Notes on Kafka and Jepsen (2013)"
        ),
    ),
    LabeledExample(
        label="quote",
        note="John Gall, section_189",
        text=(
            "A complex system that works is invariably found to have evolved from a "
            "simple system that works. The inverse proposition also appears to be "
            "true: A complex system designed from scratch never works and cannot "
            "be made to work. —John Gall, Systemantics (1975)"
        ),
    ),
]


# --- Concept-style prose (sections 1-10). Should NOT classify as quote. ---

CONCEPT_EXAMPLES: list[LabeledExample] = [
    LabeledExample(
        label="concept",
        note="data-intensive intro, section_2",
        text=(
            "Many applications today are data-intensive, as opposed to "
            "compute-intensive. Raw CPU power is rarely a limiting factor for "
            "these applications—bigger problems are usually the amount of data, "
            "the complexity of data, and the speed at which it is changing."
        ),
    ),
    LabeledExample(
        label="concept",
        note="abstractions paragraph, section_2",
        text=(
            "If that sounds painfully obvious, that's just because these data "
            "systems are such a successful abstraction: we use them all the time "
            "without thinking too much. When building an application, most "
            "engineers wouldn't dream of writing a new data storage engine from "
            "scratch, because databases are a perfectly good tool for the job."
        ),
    ),
    LabeledExample(
        label="concept",
        note="thinking about data systems, section_3",
        text=(
            "We typically think of databases, queues, caches, etc. as being very "
            "different categories of tools. Although a database and a message "
            "queue have some superficial similarity—both store data for some "
            "time—they have very different access patterns, which means "
            "different performance characteristics, and thus very different "
            "implementations."
        ),
    ),
    LabeledExample(
        label="concept",
        note="composite data systems, section_3",
        text=(
            "When you combine several tools in order to provide a service, the "
            "service's interface or application programming interface (API) "
            "usually hides those implementation details from clients. Now you "
            "have essentially created a new, special-purpose data system from "
            "smaller, general-purpose components."
        ),
    ),
    LabeledExample(
        label="concept",
        note="reliability definition",
        text=(
            "The system should continue to work correctly (performing the correct "
            "function at the desired level of performance) even in the face of "
            "adversity (hardware or software faults, and even human error)."
        ),
    ),
    LabeledExample(
        label="concept",
        note="scalability definition",
        text=(
            "As the system grows (in data volume, traffic volume, or complexity), "
            "there should be reasonable ways of dealing with that growth."
        ),
    ),
    LabeledExample(
        label="concept",
        note="hardware faults",
        text=(
            "Hard disks crash, RAM becomes faulty, the power grid has a "
            "blackout, someone unplugs the wrong network cable. These hardware "
            "faults happen all the time when you have a lot of machines."
        ),
    ),
]


# --- Abstract / transitional content. No visualisable concrete idea. ---

ABSTRACT_EXAMPLES: list[LabeledExample] = [
    LabeledExample(
        label="abstract",
        note="transition to next chapters",
        text=(
            "In the following chapters we will continue layer by layer, looking "
            "at different design decisions that need to be considered when "
            "working on a data-intensive application."
        ),
    ),
    LabeledExample(
        label="abstract",
        note="generic call-back",
        text=(
            "As we discussed earlier in this chapter, there are many factors "
            "involved here."
        ),
    ),
    LabeledExample(
        label="abstract",
        note="summary closer",
        text=(
            "In summary, these are the three concerns we will focus on for the "
            "rest of the book."
        ),
    ),
]


ALL_EXAMPLES: list[LabeledExample] = (
    QUOTE_EXAMPLES + CONCEPT_EXAMPLES + ABSTRACT_EXAMPLES
)


# --- Run + report ---


def run_eval() -> tuple[int, int, dict[str, dict[str, int]]]:
    """Run the classifier over every labeled example. Return (correct, total,
    confusion_matrix) where confusion_matrix[expected][predicted] = count.
    """
    confusion: dict[str, dict[str, int]] = {
        label: {pred: 0 for pred in VALID_TYPES} for label in VALID_TYPES
    }
    correct = 0
    total = 0

    for ex in ALL_EXAMPLES:
        predicted = classify_paragraph(ex.text)
        confusion[ex.label][predicted] = confusion[ex.label].get(predicted, 0) + 1
        total += 1
        mark = "✓" if predicted == ex.label else "✗"
        correct += 1 if predicted == ex.label else 0
        snippet = ex.text[:80].replace("\n", " ")
        print(f"  {mark} expected={ex.label:8s} predicted={predicted:8s}  [{ex.note}]")
        print(f"      {snippet}{'…' if len(ex.text) > 80 else ''}")

    return correct, total, confusion


def print_confusion(confusion: dict[str, dict[str, int]]) -> None:
    print()
    print("Confusion matrix (rows = expected, cols = predicted):")
    header = "        " + " ".join(f"{p:>9}" for p in VALID_TYPES)
    print(header)
    for expected in VALID_TYPES:
        row = " ".join(f"{confusion[expected][p]:>9d}" for p in VALID_TYPES)
        print(f"  {expected:6s} {row}")


def main() -> None:
    print(f"Running classifier eval over {len(ALL_EXAMPLES)} labeled examples...\n")
    correct, total, confusion = run_eval()
    print()
    accuracy = correct / total if total else 0.0
    print(f"Accuracy: {correct}/{total} = {accuracy:.1%}")
    print_confusion(confusion)


if __name__ == "__main__":
    main()
