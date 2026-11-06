"""Group similar failures, so that 40 failures become 4 patterns.

The default embedder is TF-IDF on words. It needs no model and no API key, and it
finds groups that share words (same task, same kind of error, same topic). A better
embedder can replace it: any object with an embed(texts) method that returns
normalized sparse vectors (dict of feature -> weight).

Clusters show patterns. They do not prove a cause.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from .datasets import render_prompt
from .failures import Failure

Vector = dict[str, float]
STOP = set("the a an of to in on at is are was were be and or for with by it its this that as from what which who how "
           "when where why do does did not no you your i me my we our they their he she answer using only context".split())
MAX_FAILURES = 2000


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[Vector]: ...


def tokens(text: str) -> list[str]:
    """Words and word pairs. Every number becomes 'num', because the exact number is a detail of one case."""
    words = ["num" if w.isdigit() else w for w in re.findall(r"[a-z0-9_']+", text.lower()) if w not in STOP]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


def _normalize(v: Vector) -> Vector:
    n = math.sqrt(sum(x * x for x in v.values()))
    return {k: x / n for k, x in v.items()} if n else v


class TfidfEmbedder:
    def embed(self, texts: list[str]) -> list[Vector]:
        docs = [Counter(tokens(t)) for t in texts]
        df = Counter(w for d in docs for w in d)
        n = len(docs)
        idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}
        return [_normalize({w: (1 + math.log(c)) * idf[w] for w, c in d.items()}) for d in docs]


def cosine(a: Vector, b: Vector) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(k, 0.0) for k, x in a.items())


def _centroid(vectors: list[Vector]) -> Vector:
    total: dict[str, float] = defaultdict(float)
    for v in vectors:
        for k, x in v.items():
            total[k] += x
    return _normalize(dict(total))


def failure_text(f: Failure) -> str:
    """What the clustering reads: the labels (repeated, so they count more), the task, the question and the answer."""
    question = render_prompt(f.record.input) if f.record and f.record.input else ""
    return " ".join([" ".join(f.labels * 3), f.task, question[:400], (f.output or "")[:200]])


def cluster_vectors(vectors: list[Vector], threshold: float = 0.3, max_rounds: int = 5) -> list[list[int]]:
    """Centroid clustering. Each item joins the closest cluster if it is similar enough. Repeats until stable.

    The order of the input does not change the result, because the code visits items in a fixed order.
    """
    order = sorted(range(len(vectors)), key=lambda i: (-len(vectors[i]), i))
    clusters: list[list[int]] = []
    centroids: list[Vector] = []
    for i in order:
        best, best_sim = -1, threshold
        for j, c in enumerate(centroids):
            s = cosine(vectors[i], c)
            if s >= best_sim:
                best, best_sim = j, s
        if best < 0:
            clusters.append([i])
            centroids.append(vectors[i])
        else:
            clusters[best].append(i)
            centroids[best] = _centroid([vectors[k] for k in clusters[best]])
    for _ in range(max_rounds):   # Move items to their nearest final centroid.
        moved = False
        new: list[list[int]] = [[] for _ in clusters]
        for i in order:
            sims = [cosine(vectors[i], c) for c in centroids]
            j = max(range(len(sims)), key=lambda k: (sims[k], -k))
            moved |= i not in clusters[j]
            new[j].append(i)
        clusters = [c for c in new if c]
        centroids = [_centroid([vectors[k] for k in c]) for c in clusters]
        if not moved:
            break
    return sorted((sorted(c) for c in clusters), key=lambda c: (-len(c), c[0]))


@dataclass
class Cluster:
    id: int
    size: int
    share_of_cases: float                # Of all cases with a result.
    top_terms: list[str]
    labels: dict[str, int]
    tasks: dict[str, int]
    models: dict[str, int]
    cohesion: float                      # Mean similarity of the members to the centre. 1 is identical.
    representatives: list[Failure] = field(repr=False, default_factory=list)
    members: list[Failure] = field(repr=False, default_factory=list)
    follow_up: str = ""


def cluster_failures(failures: list[Failure], total_cases: int, embedder: Embedder | None = None,
                     threshold: float = 0.3, min_size: int = 2) -> tuple[list[Cluster], list[Failure]]:
    """Returns (clusters of at least min_size, failures that belong to no cluster)."""
    failures = [f for f in failures if "evaluation_error" not in f.labels][:MAX_FAILURES]
    if not failures:
        return [], []
    vectors = (embedder or TfidfEmbedder()).embed([failure_text(f) for f in failures])
    groups = cluster_vectors(vectors, threshold)
    label_words = {w for f in failures for lab in f.labels for w in [lab]}
    clusters, loose = [], []
    for g in groups:
        if len(g) < min_size:
            loose += [failures[i] for i in g]
            continue
        centre = _centroid([vectors[i] for i in g])
        weights = sorted(centre.items(), key=lambda kv: -kv[1])
        terms = [k for k, _ in weights if k not in label_words and " " not in k][:5]
        sims = {i: cosine(vectors[i], centre) for i in g}
        members = [failures[i] for i in g]
        top_label = Counter(l for m in members for l in m.labels).most_common(1)[0][0]
        clusters.append(Cluster(
            id=len(clusters) + 1, size=len(g), share_of_cases=len(g) / total_cases if total_cases else 0.0,
            top_terms=terms, labels=dict(Counter(l for m in members for l in m.labels)),
            tasks=dict(Counter(m.task for m in members)), models=dict(Counter(m.model for m in members)),
            cohesion=min(1.0, sum(sims.values()) / len(g)),
            representatives=[failures[i] for i in sorted(g, key=lambda i: -sims[i])[:3]], members=members,
            follow_up=f"Add more test cases about: {', '.join(terms[:3]) or 'this pattern'} (failure type: {top_label})."))
    return clusters, loose
