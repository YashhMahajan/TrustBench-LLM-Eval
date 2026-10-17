"""A small stored summary of each run, so a list of runs does not load every run.

Loading a run reads all its rows. Listing 30 runs that way is slow. We compute the summary once,
when a run ends or is scored again, and keep it in the run row.
"""

from sqlalchemy.orm import sessionmaker

from .db import EvaluationRun
from .rundata import RunData, load_run
from .scorecard import scorecard


def compute_overview(rd: RunData) -> dict:
    out = {}
    for model in rd.models:
        card = scorecard(rd.for_model(model))
        out[model] = {"rate": card.overall.rate, "ci": list(card.overall.ci) if card.overall.ci else None,
                      "n": card.overall.n}
    return out


def refresh_overview(factory: sessionmaker, run_id: str) -> dict:
    overview = compute_overview(load_run(factory, run_id))
    with factory.begin() as s:
        run = s.get(EvaluationRun, run_id)
        if run is not None:
            run.overview = overview
    return overview


def get_overview(factory: sessionmaker, run: EvaluationRun) -> dict:
    """The stored summary. A run from an older version has none: compute it once and keep it."""
    if run.overview is not None:
        return run.overview
    return refresh_overview(factory, run.id)
