"""Checks on the project itself: the mistakes that fast-written projects often have."""

import re
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def pkg(requirement: str) -> str:
    return re.split(r"[\[<>=!~ ;]", requirement, maxsplit=1)[0]


def declared():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    return [pkg(r) for r in data["dependencies"]]


def test_every_declared_dependency_is_a_real_installed_package():
    """A name an AI model invented, or a misspelled one, fails here. Installing such a name is a supply chain risk."""
    for name in declared():
        assert metadata.version(name), name


def test_every_dependency_has_a_lower_bound():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    for r in data["dependencies"]:
        assert re.search(r">=\d", r), f"{r} has no minimum version"


def test_the_lock_file_pins_exactly_what_we_run_and_the_image_uses_it():
    lock = {}
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        if "==" in line:
            name, _, version = line.partition("==")
            lock[name.lower().replace("_", "-")] = version
    for name in declared():
        key = name.lower().replace("_", "-")
        assert key in lock, f"{name} is not in requirements.lock"
        assert metadata.version(name) == lock[key], f"{name}: the lock file is out of date. Run: pip freeze --exclude-editable > requirements.lock"
    assert "-c requirements.lock" in (ROOT / "Dockerfile").read_text()


SECRET_PATTERNS = {
    "OpenAI-style key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "Anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


# These tests plant FAKE secrets on purpose, to prove that the redactor removes them.
PLANTED = {"test_hygiene.py", "test_redact_crypto.py"}


def tracked_text_files():
    skip = {".venv", "node_modules", ".next", ".git", ".ruff_cache", ".pytest_cache", "__pycache__"}
    for path in ROOT.rglob("*"):
        if path.is_file() and not skip & set(path.relative_to(ROOT).parts) and path.suffix in {
                ".py", ".md", ".ts", ".tsx", ".json", ".yml", ".yaml", ".toml", ".env", ".example", ".txt", ".lock", ".mjs", ".sh"}:
            yield path


def test_no_secret_is_written_in_the_repository():
    for path in tracked_text_files():
        if path.name in PLANTED:
            continue
        text = path.read_text(errors="ignore")
        for label, pattern in SECRET_PATTERNS.items():
            assert not pattern.search(text), f"{path.relative_to(ROOT)} looks like it holds a {label}"


def test_env_files_are_not_tracked_and_the_example_has_no_real_values():
    ignore = (ROOT / ".gitignore").read_text()
    assert ".env" in ignore
    for line in (ROOT / ".env.example").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            assert len(value.strip()) < 24, f"{key} in .env.example looks like a real value"


@pytest.mark.parametrize("name", ["cors", "allow_origins"])
def test_the_api_does_not_open_cors_to_every_site(name):
    """The dashboard calls the API from its own server, not from the browser, so no CORS is needed. '*' would be a hole."""
    source = (ROOT / "src/trustbench/api.py").read_text() + (ROOT / "src/trustbench/api_data.py").read_text()
    assert name not in source.lower()
