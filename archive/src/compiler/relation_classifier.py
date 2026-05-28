"""
Classifies verbs/relations into arrow types or no-arrow.

Used by the DSL compiler to decide whether to draw an arrow between entities.

Categories:
  CAUSAL        → directional arrow (A causes B)
  DEPENDENCY    → directional arrow (A depends on B)
  PRODUCTION    → directional arrow (A generates B)
  CONSUMPTION   → directional arrow (A reads from B)
  COMMUNICATION → directional arrow (A sends to B)
  CONTAINMENT   → directional arrow (A stores B)
  TRANSFORMATION→ directional arrow (A converts to B)
  COMPARISON    → no arrow, side by side
  ATTRIBUTION   → no arrow, just label
  DESCRIPTION   → no arrow, just label
"""

CAUSAL_VERBS = {
    "causes", "cause", "leads to", "lead to", "results in", "result in",
    "triggers", "trigger", "forces", "force", "initiates", "initiate",
    "produces failure", "cascades", "propagates",
}

DEPENDENCY_VERBS = {
    "depends on", "depend on", "requires", "require", "needs", "need",
    "relies on", "rely on", "assumes", "assume", "expects", "expect",
    "must have", "demands", "demand",
}

PRODUCTION_VERBS = {
    "generates", "generate", "creates", "create", "produces", "produce",
    "builds", "build", "outputs", "output", "emits", "emit",
    "constructs", "construct", "yields", "yield", "returns", "return",
}

CONSUMPTION_VERBS = {
    "reads from", "read from", "consumes", "consume", "uses", "use",
    "processes", "process", "ingests", "ingest", "fetches", "fetch",
    "loads", "load", "queries", "query", "accesses", "access",
}

COMMUNICATION_VERBS = {
    "sends to", "send to", "writes to", "write to", "delivers", "deliver",
    "forwards", "forward", "publishes", "publish", "pushes", "push",
    "notifies", "notify", "broadcasts", "broadcast", "routes", "route",
    "transfers", "transfer", "replicates", "replicate",
}

CONTAINMENT_VERBS = {
    "stores", "store", "contains", "contain", "holds", "hold",
    "includes", "include", "keeps", "keep", "maintains", "maintain",
    "caches", "cache", "buffers", "buffer", "indexes", "index",
}

TRANSFORMATION_VERBS = {
    "converts", "convert", "transforms", "transform", "maps to", "map to",
    "compiles", "compile", "encodes", "encode", "decodes", "decode",
    "serializes", "serialize", "deserializes", "deserialize",
    "translates", "translate", "normalizes", "normalize",
    "partitions", "partition", "rebalances", "rebalance",
}

# these do NOT get arrows
COMPARISON_VERBS = {
    "as opposed to", "versus", "compared to", "unlike", "rather than",
    "in contrast to", "different from", "similar to", "same as",
}

ATTRIBUTION_VERBS = {
    "is", "are", "was", "were", "has", "have", "had",
    "called", "known as", "named", "defined as", "means",
}

DESCRIPTION_VERBS = {
    "such as", "for example", "like", "including", "e.g.",
    "for instance", "namely",
}

# all arrow-worthy verb sets with their category name
ARROW_CATEGORIES = {
    "CAUSAL": CAUSAL_VERBS,
    "DEPENDENCY": DEPENDENCY_VERBS,
    "PRODUCTION": PRODUCTION_VERBS,
    "CONSUMPTION": CONSUMPTION_VERBS,
    "COMMUNICATION": COMMUNICATION_VERBS,
    "CONTAINMENT": CONTAINMENT_VERBS,
    "TRANSFORMATION": TRANSFORMATION_VERBS,
}

NO_ARROW_CATEGORIES = {
    "COMPARISON": COMPARISON_VERBS,
    "ATTRIBUTION": ATTRIBUTION_VERBS,
    "DESCRIPTION": DESCRIPTION_VERBS,
}


def classify_relation(verb: str) -> tuple[str, bool]:
    """
    Classify a verb/relation.
    Returns (category, needs_arrow).
    """
    lowered = verb.lower().strip()

    # check no-arrow categories first (more specific)
    for category, verbs in NO_ARROW_CATEGORIES.items():
        if lowered in verbs:
            return (category, False)

    # check arrow categories
    for category, verbs in ARROW_CATEGORIES.items():
        if lowered in verbs:
            return (category, True)

    # partial match — check if any arrow verb is contained in the input
    for category, verbs in ARROW_CATEGORIES.items():
        for v in verbs:
            if v in lowered or lowered in v:
                return (category, True)

    # partial match for no-arrow
    for category, verbs in NO_ARROW_CATEGORIES.items():
        for v in verbs:
            if v in lowered or lowered in v:
                return (category, False)

    # default: unknown verbs don't get arrows
    return ("UNKNOWN", False)


def needs_arrow(verb: str) -> bool:
    """Simple check: does this verb warrant an arrow?"""
    _, arrow = classify_relation(verb)
    return arrow
