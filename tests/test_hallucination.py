from trustbench.hallucination import claim_metrics, pool
from trustbench.verify import ClaimVerdict


def verdicts(*labels):
    return [ClaimVerdict(f"c{i}", label) for i, label in enumerate(labels)]


def test_rates_use_assessed_claims_as_denominator():
    m = claim_metrics(verdicts("supported", "supported", "insufficient", "contradicted", "not_applicable"))
    assert m.total == 5 and m.assessed == 4
    assert (m.evidence_coverage, m.unsupported_claim_rate, m.contradiction_rate) == (0.5, 0.25, 0.25)
    assert m.unassessed_rate == 0.2


def test_unassessable_claims_are_not_counted_as_supported():
    m = claim_metrics(verdicts("not_applicable", "not_applicable"))
    assert m.evidence_coverage is None and m.unassessed_rate == 1.0


def test_empty_answer_has_no_rates():
    m = claim_metrics([])
    assert m.unsupported_claim_rate is None and m.unassessed_rate is None


def test_review_count_and_dict():
    v = verdicts("supported", "insufficient")
    v[1].needs_review = True
    d = claim_metrics(v).to_dict()
    assert d["needs_review"] == 1 and d["ec"] == 0.5


def test_pooling_weighs_each_claim_equally():
    big = claim_metrics(verdicts(*["supported"] * 9))
    small = claim_metrics(verdicts("insufficient"))
    assert pool([big, small]).evidence_coverage == 0.9
