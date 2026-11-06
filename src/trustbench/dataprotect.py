"""The data policy of a workspace: what we keep of the production traffic, and for how long.

The defaults are the safest. We store NO content, only measurements (time, size, cost, status).
To keep the text of prompts and answers, an admin turns store_content on. Then the text is
redacted (unless redact is off) and encrypted before it reaches the database.
"""

import hashlib
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import sessionmaker

from . import crypto
from .db import Workspace
from .redact import RedactionError, compile_custom, redact


class DataPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_content: bool = False                      # Keep the text of inputs and outputs?
    redact: bool = True                              # Remove personal data and secrets before storing?
    retention_days: int = Field(default=30, ge=1, le=3650)
    sample_rate: float = Field(default=0.1, ge=0, le=1)      # Share of traces that get an online evaluation.
    max_payload_chars: int = Field(default=20000, ge=100, le=1_000_000)
    extra_redactions: list[str] = []                 # More regular expressions to remove.
    online_evaluators: list[dict] = []               # Evaluator specs for online evaluation. See trustbench.online.
    online_judge: str | None = None                  # provider:model for judge-based online evaluators.

    @field_validator("extra_redactions")
    @classmethod
    def _valid_patterns(cls, v):
        try:
            compile_custom(v)
        except RedactionError as e:
            raise ValueError(str(e)) from e
        return v


    @model_validator(mode="after")
    def _online(self):
        from .online import check_specs
        problem = check_specs(self.online_evaluators, self.online_judge)
        if problem:
            raise ValueError(problem)
        return self


class PolicyProblem(ValueError):
    pass


def policy_of(ws: Workspace) -> DataPolicy:
    try:
        return DataPolicy(**(ws.settings or {}))
    except ValidationError as e:
        raise PolicyProblem("The stored data policy is not valid: " + "; ".join(x["msg"] for x in e.errors())) from e


def update_policy(factory: sessionmaker, workspace_id: str, **changes) -> DataPolicy:
    """Change settings. The code checks all of them together. It saves nothing if one is wrong."""
    with factory.begin() as s:
        ws = s.get(Workspace, workspace_id)
        if ws is None:
            raise PolicyProblem(f"No workspace '{workspace_id}'.")
        try:
            new = DataPolicy(**{**policy_of(ws).model_dump(), **changes})
        except ValidationError as e:
            raise PolicyProblem("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from e
        if new.online_evaluators and not crypto.available():
            raise PolicyProblem("Online evaluation keeps the redacted text in encrypted tasks until they are done. "
                                "Set TRUSTBENCH_SECRET_KEY first (make one with: trustbench secrets generate-key).")
        if new.store_content and not crypto.available():
            raise PolicyProblem("store_content needs an encryption key. Set TRUSTBENCH_SECRET_KEY first "
                                "(make one with: trustbench secrets generate-key).")
        ws.settings = new.model_dump()
        return new


@dataclass
class Protected:
    text: str | None            # Always None. We never keep plain text.
    ciphertext: str | None      # The redacted text, encrypted. None when we store no content.
    clean: str                  # The redacted text, in memory only. For online evaluation.
    content_hash: str           # Hash of the redacted text. It lets us find repeats without the text.
    length: int                 # Length of the original text.
    truncated: bool
    redactions: dict[str, int]


def protect(policy: DataPolicy, text: str) -> Protected:
    """Apply the policy to one piece of text. The code never keeps the original text."""
    original_length = len(text)
    truncated = original_length > policy.max_payload_chars
    clean, counts = redact(text[:policy.max_payload_chars], policy.extra_redactions) if policy.redact else (text[:policy.max_payload_chars], {})
    digest = hashlib.sha256(clean.encode()).hexdigest()
    if not policy.store_content:
        return Protected(None, None, clean, digest, original_length, truncated, counts)
    return Protected(None, crypto.encrypt_text(clean), clean, digest, original_length, truncated, counts)
