"""A release policy: the rules a candidate must meet before it can ship."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

Score01 = Field(default=None, ge=0, le=1)


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_score: float | None = Score01      # Pass rate must reach this.
    maximum_regression: float | None = Score01  # Pass rate may fall at most this far below the baseline.
    min_cases: int = Field(default=1, ge=1)    # Cases needed before we trust a result.


class ReleasePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "default"
    min_cases: int = Field(default=30, ge=1)   # Scored cases needed in total.
    confidence_bound: bool = True              # Judge minimum_score by the low end of its interval.
    on_insufficient_evidence: Literal["block", "allow"] = "block"
    min_effect: float = Field(default=0.01, ge=0, le=1)
    overall: Rule = Rule()
    dimensions: dict[str, Rule] = {}           # Keyed by task name.
    critical_failures_allowed: int | None = Field(default=0, ge=0)
    latency_p95_max_seconds: float | None = Field(default=None, gt=0)
    cost_per_success_max_usd: float | None = Field(default=None, gt=0)
    call_failure_rate_max: float | None = Score01


class PolicyError(ValueError):
    pass


def parse_policy(text: str) -> ReleasePolicy:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise PolicyError(f"The policy is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise PolicyError("The policy must be a YAML mapping.")
    data = data.get("release_policy", data)
    try:
        return ReleasePolicy(**data)
    except ValidationError as e:
        lines = [f"  {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise PolicyError("The policy has problems:\n" + "\n".join(lines)) from e


def load_policy(path: Path) -> ReleasePolicy:
    if not path.exists():
        raise PolicyError(f"Policy file not found: {path}. Create one with 'trustbench policy init'.")
    return parse_policy(path.read_text())


TEMPLATE = """\
# TrustBench release policy. 'trustbench gate' checks a run against these rules.
# A rule you remove is not checked.
release_policy:
  name: production

  # Scored cases needed before any result can be trusted.
  min_cases: 50

  # true: a minimum score is met only when the LOW end of the 95% interval reaches it.
  confidence_bound: true

  # What to do when the evidence is too weak to decide.
  #   block = stop the release (exit code 1)    allow = let it pass, but still report "inconclusive"
  on_insufficient_evidence: block

  # The smallest change in pass rate that matters (0.01 = 1 point).
  min_effect: 0.01

  overall:
    minimum_score: 0.90
    maximum_regression: 0.01     # Needs a baseline: 'trustbench baseline set'

  # Rules for one task (dimension). The name is the task name in your dataset.
  dimensions:
    grounded_qa:
      minimum_score: 0.92
      min_cases: 20

  # Cases marked "critical": true in their metadata. This many failures are allowed.
  critical_failures_allowed: 0

  latency_p95_max_seconds: 5.0
  cost_per_success_max_usd: 0.05
  call_failure_rate_max: 0.02
"""
