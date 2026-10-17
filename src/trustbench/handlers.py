"""What the workers do. Each handler gets the payload of a task."""

from sqlalchemy.orm import sessionmaker

from .jobs import Handler


def default_handlers(factory: sessionmaker) -> dict[str, Handler]:
    return {}
