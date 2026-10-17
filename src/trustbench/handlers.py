"""What the workers do. Each handler gets the payload of a task."""

from sqlalchemy.orm import sessionmaker

from .jobs import Handler
from .online import KIND_JOB, evaluate_trace_handler


def default_handlers(factory: sessionmaker, provider_factory=None) -> dict[str, Handler]:
    return {KIND_JOB: evaluate_trace_handler(factory, provider_factory)}
