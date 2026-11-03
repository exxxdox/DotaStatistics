"""Keep local secrets and exported images out of Docker build contexts."""

from pathlib import Path

import pytest


DOCKERIGNORE = Path(__file__).resolve().parents[1] / ".dockerignore"


@pytest.mark.parametrize("pattern", [".env", ".env.*", "dist", "*.tar", "**/__pycache__", "**/*.py[cod]"])
def test_build_context_excludes_private_files_and_archives(pattern: str) -> None:
    rules = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    assert pattern in rules
    assert f"!{pattern}" not in rules


def test_build_context_keeps_example_environment_file() -> None:
    rules = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    # The safe template must be allowed after the broader secret-file rule.
    assert rules.index(".env.*") < rules.index("!.env.example")
