"""Does the confidence of a model match how often it is right?

A model that says "90% sure" should be right about 90% of the time.
We also ask: can its confidence tell us which answers to trust?
"""

from dataclasses import dataclass, field

from .stats import bootstrap_ci, mean


def brier_score(conf: list[float], correct: list[bool]) -> float:
    """Mean squared gap between the confidence and the result (1 or 0). 0 is perfect. 0.25 is a coin flip at 50%."""
    return mean((c - float(k)) ** 2 for c, k in zip(conf, correct))


def calibration_bins(conf: list[float], correct: list[bool], n_bins: int = 10) -> list[dict]:
    bins = [[] for _ in range(n_bins)]
    for c, k in zip(conf, correct):
        bins[min(int(c * n_bins), n_bins - 1)].append((c, k))
    return [{"low": i / n_bins, "high": (i + 1) / n_bins, "n": len(b),
             "mean_confidence": mean(c for c, _ in b), "accuracy": mean(float(k) for _, k in b)}
            for i, b in enumerate(bins) if b]


def expected_calibration_error(conf: list[float], correct: list[bool], n_bins: int = 10) -> float:
    """Weighted average gap between confidence and accuracy in each confidence bin.

    Limits: it depends on the number of bins, and with few answers the bins are noisy.
    Read it together with the Brier score and the number of answers.
    """
    n = len(conf)
    return sum(b["n"] / n * abs(b["mean_confidence"] - b["accuracy"]) for b in calibration_bins(conf, correct, n_bins))


def auroc(conf: list[float], correct: list[bool]) -> float | None:
    """The chance that a right answer has higher confidence than a wrong one. 0.5 means the confidence tells us nothing."""
    right = [c for c, k in zip(conf, correct) if k]
    wrong = [c for c, k in zip(conf, correct) if not k]
    if not right or not wrong:
        return None
    wins = sum((r > w) + 0.5 * (r == w) for r in right for w in wrong)
    return wins / (len(right) * len(wrong))


def risk_coverage(conf: list[float], correct: list[bool]) -> list[tuple[float, float]]:
    """If we answer only the most confident share of questions, how many of those answers are wrong?

    Returns (coverage, risk) points. Answers with equal confidence enter together.
    """
    n = len(conf)
    order = sorted(zip(conf, correct), key=lambda x: -x[0])
    points, errors, i = [], 0, 0
    while i < n:
        j = i
        while j < n and order[j][0] == order[i][0]:
            errors += not order[j][1]
            j += 1
        points.append((j / n, errors / j))
        i = j
    return points


def selective_risk(conf: list[float], correct: list[bool], coverage: float) -> float | None:
    """Error rate when we answer the most confident answers that fill at most this share of all questions."""
    ok = [p for p in risk_coverage(conf, correct) if p[0] <= coverage + 1e-9]
    return ok[-1][1] if ok else None


@dataclass
class CalibrationReport:
    n: int = 0
    unreadable: int = 0                 # Answers with no readable confidence.
    accuracy: float | None = None
    mean_confidence: float | None = None
    brier: float | None = None
    brier_ci: tuple[float, float] | None = None
    ece: float | None = None
    auroc: float | None = None
    risk_at_50: float | None = None
    risk_at_80: float | None = None
    bins: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


MIN_ANSWERS = 50


def calibration_report(conf: list[float], correct: list[bool], unreadable: int = 0) -> CalibrationReport:
    r = CalibrationReport(n=len(conf), unreadable=unreadable)
    if unreadable:
        r.warnings.append(f"{unreadable} answer(s) had no readable confidence and are not counted.")
    if not conf:
        return r
    r.accuracy, r.mean_confidence = mean(float(k) for k in correct), mean(conf)
    r.brier = brier_score(conf, correct)
    sq = [(c - float(k)) ** 2 for c, k in zip(conf, correct)]
    r.brier_ci = bootstrap_ci(sq)
    r.ece, r.auroc, r.bins = expected_calibration_error(conf, correct), auroc(conf, correct), calibration_bins(conf, correct)
    r.risk_at_50, r.risk_at_80 = selective_risk(conf, correct, 0.5), selective_risk(conf, correct, 0.8)
    if r.n < MIN_ANSWERS:
        r.warnings.append(f"Only {r.n} answers. Use at least {MIN_ANSWERS}. The calibration error is noisy with few answers.")
    if r.auroc is None:
        r.warnings.append("All answers are right, or all are wrong, so AUROC is undefined.")
    return r


def calibration_from_records(records) -> CalibrationReport:
    """Build the report from the 'calibrated_answer' results of a run."""
    conf, correct, unreadable = [], [], 0
    for rec in records:
        for res in rec.results:
            if res["evaluator"] != "calibrated_answer":
                continue
            d = res["details"]
            if d.get("confidence") is None:
                unreadable += 1
            else:
                conf.append(d["confidence"])
                correct.append(bool(d["correct"]))
    return calibration_report(conf, correct, unreadable)
