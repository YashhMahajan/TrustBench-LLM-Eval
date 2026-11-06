"""What does this system cost per year, with its mistakes? A cost model turns pass rates into dollars.

You tell us what a mistake costs, how many items you handle, and how many really need action:

    cost_model:
      volume_per_year: 1000000
      positive_rate: 0.05             # 5% of real items need action (for example an audit)
      costs: {false_negative: 30.0, false_positive: 2.0, true_positive: 2.0, true_negative: 0.0}
      manual: {fp_rate: 0.02, fn_rate: 0.03, cost_per_item: 0.20}      # what you pay today (optional)

Mark each test case with metadata `positive: true` (a real item that needs action) or `positive: false`.
A failed case on a positive case is a miss (false negative). A failed case on a negative case is a false alarm.
So the evaluators you already wrote decide what counts as a mistake.

Interval: we take the 95% interval of each error rate (Wilson) and compute the cost at the corners of the box.
This is conservative: it covers at least 90% jointly, not 95%.
Cost of the system itself is the measured mean cost per item, or `processing_cost_per_item` if you set it.
If it is unknown, the total is `incomplete`. We never count an unknown cost as zero.
"""

from dataclasses import dataclass, field
from itertools import product

from pydantic import BaseModel, ConfigDict, Field

from ..rundata import Record
from ..stats import wilson_interval

Rate = Field(ge=0, le=1)


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    false_positive: float = Field(default=0.0, ge=0)    # A wrong alarm. For example a human audit of a valid receipt.
    false_negative: float = Field(default=0.0, ge=0)    # A miss. For example a bad receipt that was paid.
    true_positive: float = Field(default=0.0, ge=0)     # Handling a correct alarm.
    true_negative: float = Field(default=0.0, ge=0)


class Manual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fp_rate: float = Rate
    fn_rate: float = Rate
    cost_per_item: float = Field(default=0.0, ge=0)


class CostModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume_per_year: int = Field(gt=0)
    positive_rate: float = Field(gt=0, lt=1)            # Share of real items that need action.
    costs: Costs = Costs()
    label_key: str = "positive"                         # The metadata key that marks a case as positive or negative.
    processing_cost_per_item: float | None = Field(default=None, ge=0)
    manual: Manual | None = None


def annual_cost(m: CostModel, fp_rate: float, fn_rate: float, processing_per_item: float) -> float:
    """Dollars per year at these error rates."""
    pos, neg, c = m.volume_per_year * m.positive_rate, m.volume_per_year * (1 - m.positive_rate), m.costs
    return (pos * (fn_rate * c.false_negative + (1 - fn_rate) * c.true_positive)
            + neg * (fp_rate * c.false_positive + (1 - fp_rate) * c.true_negative)
            + m.volume_per_year * processing_per_item)


@dataclass
class CostReport:
    status: str                                   # ok | incomplete
    reason: str = ""                              # Why it is incomplete.
    fn_rate: float | None = None
    fp_rate: float | None = None
    fn_ci: tuple[float, float] | None = None
    fp_ci: tuple[float, float] | None = None
    positives: int = 0                            # Executions on positive cases.
    negatives: int = 0
    unlabeled: int = 0                            # Executions on cases with no label. Left out.
    processing_per_item: float | None = None
    annual_cost: float | None = None
    annual_ci: tuple[float, float] | None = None
    without_processing: float | None = None       # Shown when the processing cost is unknown.
    manual_cost: float | None = None
    perfect_cost: float | None = None
    savings: float | None = None                  # Manual minus system. Positive means the system is cheaper.
    savings_ci: tuple[float, float] | None = None
    notes: list[str] = field(default_factory=list)


def _failed(r: Record) -> bool | None:
    """True for a miss or wrong output, False for a pass, None when the case says nothing about the system."""
    if r.status == "failed":
        return True                               # No answer is a mistake.
    if r.status == "done" and r.passed is not None:
        return not r.passed
    return None


def cost_report(m: CostModel, records: list[Record]) -> CostReport:
    perfect = annual_cost(m, 0.0, 0.0, 0.0)
    manual = annual_cost(m, m.manual.fp_rate, m.manual.fn_rate, m.manual.cost_per_item) if m.manual else None
    rep = CostReport("ok", perfect_cost=perfect, manual_cost=manual)
    pos_fail = pos_n = neg_fail = neg_n = 0
    for r in records:
        label, failed = r.metadata.get(m.label_key), _failed(r)
        if failed is None:
            continue
        if label is True:
            pos_n, pos_fail = pos_n + 1, pos_fail + failed
        elif label is False:
            neg_n, neg_fail = neg_n + 1, neg_fail + failed
        else:
            rep.unlabeled += 1
    rep.positives, rep.negatives = pos_n, neg_n
    if rep.unlabeled:
        rep.notes.append(f"{rep.unlabeled} executions have no '{m.label_key}' label and are left out.")
    if not pos_n or not neg_n:
        missing = "positive" if not pos_n else "negative"
        return CostReport("incomplete", f"No scored {missing} cases. Mark cases with metadata '{m.label_key}': true or false.",
                          positives=pos_n, negatives=neg_n, unlabeled=rep.unlabeled, perfect_cost=perfect, manual_cost=manual,
                          notes=rep.notes)
    rep.fn_rate, rep.fp_rate = pos_fail / pos_n, neg_fail / neg_n
    rep.fn_ci, rep.fp_ci = wilson_interval(pos_fail, pos_n), wilson_interval(neg_fail, neg_n)

    if m.processing_cost_per_item is not None:
        rep.processing_per_item = m.processing_cost_per_item
    else:
        done = [r for r in records if r.status == "done"]
        if done and all(r.cost_usd is not None for r in done):
            rep.processing_per_item = sum(r.cost_usd for r in done) / len(done)
    zero_processing = annual_cost(m, rep.fp_rate, rep.fn_rate, 0.0)
    if rep.processing_per_item is None:
        rep.status = "incomplete"
        rep.reason = ("The cost of running the system is unknown (no price for the model, or an app that reports no tokens). "
                      "Set processing_cost_per_item in the cost model.")
        rep.without_processing = zero_processing
        return rep
    rep.annual_cost = annual_cost(m, rep.fp_rate, rep.fn_rate, rep.processing_per_item)
    corners = [annual_cost(m, fp, fn, rep.processing_per_item) for fp, fn in product(rep.fp_ci, rep.fn_ci)]
    rep.annual_ci = (min(corners), max(corners))
    if manual is not None:
        rep.savings = manual - rep.annual_cost
        rep.savings_ci = (manual - rep.annual_ci[1], manual - rep.annual_ci[0])
    return rep
