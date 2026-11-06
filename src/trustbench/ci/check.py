"""`trustbench ci check`: run the candidate, ask the gate, build the comment.

The candidate and its baseline are targets in trustbench.yaml, for example:

    targets:
      main:      python:myapp.agent:answer@main-version
      candidate: python:myapp.agent:answer@pr
    dataset: datasets/support.jsonl
    policy: policy.yaml

    trustbench ci check --target candidate --baseline-target main

Both run on the same dataset version in the same job. So the comparison uses pairs. With no --baseline-target, TrustBench uses the `baseline:` name in the config, if it exists in the database. With neither, only the policy rules apply.
"""

from dataclasses import dataclass, field
from pathlib import Path

from .. import baselines
from ..edd.config import EddConfig
from ..edd.runner import RunSetupError, load_dataset, read_prompt, run_options
from ..engine import run_eval
from ..gate import evaluate_gate
from ..policy import PolicyError, ReleasePolicy, load_policy
from ..rundata import load_run
from .comment import build_comment


class CheckError(ValueError):
    """The check cannot run. The message says why, in words for the user."""


@dataclass
class CheckResult:
    run_id: str
    baseline_run_id: str | None
    status: str | None                   # The gate status, or None when there is no policy.
    allowed: bool
    comment: str
    checks: list = field(default_factory=list)
    pass_rate: float | None = None

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "baseline_run_id": self.baseline_run_id, "status": self.status, "allowed": self.allowed,
                "pass_rate": self.pass_rate, "checks": self.checks}


def _one(cfg: EddConfig, name: str | None, what: str) -> str:
    try:
        specs = cfg.specs([name] if name else None)
    except ValueError as e:
        raise CheckError(str(e)) from e
    if len(specs) != 1:
        raise CheckError(f"Say which {what} to use with --{'target' if what == 'target' else 'baseline-target'} NAME. "
                         f"The config has {len(specs)}.")
    return specs[0]


def _policy(cfg: EddConfig, path: Path | None) -> ReleasePolicy | None:
    path = path or Path(cfg.policy)
    if not path.exists():
        return None
    try:
        return load_policy(path)
    except PolicyError as e:
        raise CheckError(str(e)) from e


def run_check(factory, cfg: EddConfig, *, target: str | None = None, baseline_target: str | None = None,
              policy_path: Path | None = None, min_effect: float = 0.01, progress=None) -> CheckResult:
    candidate = _one(cfg, target, "target")
    if not cfg.dataset:
        raise CheckError("The config has no dataset. Add 'dataset: path/to/cases.jsonl' to trustbench.yaml.")
    policy = _policy(cfg, policy_path)
    try:
        version, _, workspace_id = load_dataset(factory, cfg.dataset)
        prompt = read_prompt(Path(cfg.run.system_prompt_file) if cfg.run.system_prompt_file else None)
    except RunSetupError as e:
        raise CheckError(str(e)) from e

    def run(spec: str, name: str) -> str:
        return run_eval(factory, version.id, [spec], run_options(cfg.run, name=name, judge=cfg.judge, system_prompt=prompt),
                        workspace_id=workspace_id, progress=progress)

    base_id = base_model = None
    if baseline_target:
        base_id = run(_one(cfg, baseline_target, "baseline target"), f"ci-baseline:{baseline_target}")
    elif cfg.baseline:
        try:
            base_id, base_model = baselines.resolve(factory, cfg.baseline)
        except ValueError as e:
            raise CheckError(f"The config names the baseline '{cfg.baseline}' but it does not exist here. "
                             "Set it with 'trustbench baseline set', or use --baseline-target.") from e
    run_id = run(candidate, f"ci:{target or 'candidate'}")

    cand, base = load_run(factory, run_id), load_run(factory, base_id) if base_id else None
    model = cand.pick_model()
    decision = evaluate_gate(policy, cand, model, base, base_model) if policy else None
    if decision:
        baselines.save_decision(factory, run_id, model, (base_id, base_model) if base_id else None, policy, decision)
    comment = build_comment(cand, model, baseline=base, baseline_model=base_model, decision=decision, policy=policy,
                            min_effect=min_effect)
    from ..scorecard import scorecard
    return CheckResult(run_id, base_id, decision.status if decision else None, decision.allowed if decision else True, comment,
                       [c.to_dict() for c in decision.checks] if decision else [], scorecard(cand.for_model(model)).overall.rate)
