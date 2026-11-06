"""trustbench.yaml: one file that says what to test and how to judge it.

    trustbench init            writes a commented file
    trustbench run             reads it. A command line flag always wins over the file.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_PATH = Path("trustbench.yaml")


class ConfigError(ValueError):
    pass


class RunSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reps: int = Field(default=1, ge=1)                 # Repetitions of each case.
    concurrency: int = Field(default=4, ge=1)
    retries: int = Field(default=2, ge=0)
    timeout: float = Field(default=60.0, gt=0)         # Seconds for each call.
    budget: float | None = Field(default=None, gt=0)   # Stop starting calls after this many USD.
    rpm: float | None = Field(default=None, gt=0)      # Most calls to start in a minute for each provider.
    cache: bool = True
    system_prompt_file: str | None = None            # A file with the system prompt. Its text is stored with the run.

    def merged(self, **flags) -> "RunSettings":
        """These settings with command line flags on top. A flag that is None means: not given."""
        return self.model_copy(update={k: v for k, v in flags.items() if v is not None})


class EddConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    dataset: str | None = None                         # A built-in suite name or a path.
    targets: dict[str, str] = {}                       # Name -> spec: provider:model, python:module:function@label, http:URL@label
    default_targets: list[str] = []                    # What `trustbench run` uses with no flags. Empty means all targets.
    judge: str | None = None                           # provider:model that grades open answers.
    policy: str = "policy.yaml"                        # The release policy that `trustbench gate check` uses.
    baseline: str | None = None                        # A baseline name for comparisons and the gate.
    run: RunSettings = RunSettings()

    @model_validator(mode="after")
    def _check(self):
        if self.version != 1:
            raise ValueError(f"version must be 1, not {self.version}.")
        for name, spec in self.targets.items():
            if ":" not in spec:
                raise ValueError(f"Target '{name}' ('{spec}') must look like provider:model.")
        unknown = [n for n in self.default_targets if n not in self.targets]
        if unknown:
            raise ValueError(f"default_targets names unknown targets: {unknown}. Known: {sorted(self.targets)}.")
        return self

    def specs(self, names: list[str] | None = None) -> list[str]:
        """Model specs for target names. With no names: the default targets, or all of them."""
        chosen = names or self.default_targets or list(self.targets)
        missing = [n for n in chosen if n not in self.targets]
        if missing:
            raise ConfigError(f"Unknown target: {missing}. Known targets: {sorted(self.targets)}.")
        return [self.targets[n] for n in chosen]


def parse_config(text: str) -> EddConfig:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"trustbench.yaml is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("trustbench.yaml must be a YAML mapping.")
    try:
        return EddConfig(**data)
    except ValidationError as e:
        lines = [f"  {'.'.join(str(p) for p in err['loc']) or 'file'}: {err['msg']}" for err in e.errors()]
        raise ConfigError("trustbench.yaml has problems:\n" + "\n".join(lines)) from e


def find_config(explicit: Path | None = None) -> Path | None:
    """The file the user named, or ./trustbench.yaml if it exists. None means: no file, use the flags."""
    if explicit is not None:
        if not explicit.exists():
            raise ConfigError(f"Config file not found: {explicit}. Create one with 'trustbench init'.")
        return explicit
    return DEFAULT_PATH if DEFAULT_PATH.exists() else None


def load_config(explicit: Path | None = None) -> EddConfig | None:
    path = find_config(explicit)
    return parse_config(path.read_text()) if path else None


TEMPLATE = """\
# TrustBench configuration. `trustbench run` reads this file. A command line flag always wins.
version: 1

# The test cases: a built-in suite name (see `trustbench run --help`) or a path to .jsonl, .json or .csv.
dataset: smoke

# What you test. Each target has a name and a spec:
#   provider:model                     a model, for example openai:gpt-4o-mini
#   python:package.module:function@v1  YOUR code. The function gets the prompt. @v1 is your version label:
#                                      change it when the behaviour changes, so old answers are not reused.
#   http:https://host/path@v1          YOUR service. We POST {"prompt", "input"} and read {"output", "tool_calls", "spans"}.
targets:
  baseline: mock:smoke
  # candidate: python:myapp.agent:answer@v1

# Targets used by a plain `trustbench run`. Leave empty to use all of them.
default_targets: [baseline]

# A model that grades open answers (needed by llm_judge, grounded_claims and the rag_* evaluators).
# judge: anthropic:claude-haiku-5-5

# The release policy for `trustbench gate check` (create one with `trustbench policy init`),
# and the baseline name that new runs are compared with (set one with `trustbench baseline set`).
policy: policy.yaml
# baseline: main

run:
  reps: 1          # Repetitions of each case. Use 3 or more to measure how stable the answers are.
  concurrency: 4
  retries: 2
  timeout: 60      # Seconds for each call.
  # budget: 5.0    # Stop starting calls after this many USD.
  # rpm: 60        # Most calls to start in one minute, for each provider.
  cache: true      # Reuse stored answers. App targets are cached by version label or git commit.
  # system_prompt_file: prompts/support_v1.txt   # Stored with each run, so `experiment compare` can show the prompt diff.
"""
