"""Hallucination metrics from claim labels.

assessed = supported + contradicted + insufficient.
Claims we could not judge (not_applicable) are NOT in the denominator and
NOT counted as supported. We report their share as unassessed_rate, so a
weak judge cannot look good by labelling few claims.
"""

from dataclasses import asdict, dataclass

from .verify import ClaimVerdict


@dataclass
class ClaimMetrics:
    total: int = 0
    supported: int = 0
    contradicted: int = 0
    insufficient: int = 0
    not_applicable: int = 0
    needs_review: int = 0

    @property
    def assessed(self) -> int:
        return self.supported + self.contradicted + self.insufficient

    def _rate(self, n: int) -> float | None:
        return n / self.assessed if self.assessed else None

    @property
    def unsupported_claim_rate(self) -> float | None:  # UCR
        return self._rate(self.insufficient)

    @property
    def contradiction_rate(self) -> float | None:  # CR
        return self._rate(self.contradicted)

    @property
    def evidence_coverage(self) -> float | None:  # EC
        return self._rate(self.supported)

    @property
    def unassessed_rate(self) -> float | None:
        return self.not_applicable / self.total if self.total else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(assessed=self.assessed, ucr=self.unsupported_claim_rate, cr=self.contradiction_rate,
                 ec=self.evidence_coverage, unassessed_rate=self.unassessed_rate)
        return d


def claim_metrics(verdicts: list[ClaimVerdict]) -> ClaimMetrics:
    m = ClaimMetrics(total=len(verdicts))
    for v in verdicts:
        setattr(m, v.label, getattr(m, v.label) + 1)
        m.needs_review += v.needs_review
    return m


def pool(metrics: list[ClaimMetrics]) -> ClaimMetrics:
    """Add up the counts of many responses. Rates then weigh each claim equally."""
    out = ClaimMetrics()
    for m in metrics:
        for f in ("total", "supported", "contradicted", "insufficient", "not_applicable", "needs_review"):
            setattr(out, f, getattr(out, f) + getattr(m, f))
    return out
