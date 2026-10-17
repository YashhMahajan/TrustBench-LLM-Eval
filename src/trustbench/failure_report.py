"""Everything about the failures of one model in one run, as plain data."""

from collections import Counter

from .clustering import Embedder, cluster_failures
from .failures import TAXONOMY, collect_failures, label_counts
from .priority import prioritize
from .rag import diagnose
from .rundata import Record, case_outcomes


def analyze_failures(records: list[Record], embedder: Embedder | None = None) -> dict:
    outcomes = case_outcomes(records)
    total = sum(o is not None for o in outcomes.values())
    failures = collect_failures(records)
    counts = label_counts(failures, total)
    clusters, loose = cluster_failures(failures, total, embedder)

    stages = Counter()
    for f in failures:
        d = diagnose(f.record)
        if d.stage:
            stages[d.stage] += 1

    by_label = {c.label: [f for f in failures if c.label in f.labels] for c in counts}
    by_cluster = {f"cluster {c.id}: {', '.join(c.top_terms[:3]) or c.follow_up}": c.members for c in clusters}
    return {
        "cases_with_a_result": total,
        "failed_cases": len(failures),
        "labels": [{"label": c.label, "meaning": TAXONOMY.get(c.label, ""), "cases": c.cases, "share": c.share,
                    "case_ids": c.case_ids} for c in counts],
        "clusters": [{"id": c.id, "size": c.size, "share": c.share_of_cases, "terms": c.top_terms, "labels": c.labels,
                      "tasks": c.tasks, "cohesion": c.cohesion, "follow_up": c.follow_up,
                      "examples": [f.case_id for f in c.representatives]} for c in clusters],
        "unclustered": [f.case_id for f in loose],
        "rag_stages": dict(stages),
        "priority": [vars(i) for i in prioritize(by_label, total)],
        "cluster_priority": [vars(i) for i in prioritize(by_cluster, total)],
        "needs_review": [f.case_id for f in failures if f.needs_review],
    }
