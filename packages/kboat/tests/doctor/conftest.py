"""Shared vault fixtures for the doctor tests.

Fixtures rather than helpers imported across test modules, so the CLI tests and
the core tests share the setup without depending on each other.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from kboat.doctor.core import REQUIRED_DIRS
from kboat.schema import QUESTIONS_FILE


@pytest.fixture
def healthy_vault(tmp_path: Path) -> Path:
    """A vault every precondition check passes."""
    for name in REQUIRED_DIRS:
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / QUESTIONS_FILE).write_text("- a question\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def evict() -> Callable[[Path, str], Path]:
    """Plant the placeholder iCloud leaves behind when it evicts a named file."""

    def plant(directory: Path, name: str) -> Path:
        placeholder = directory / f".{name}.icloud"
        placeholder.write_bytes(b"")
        return placeholder

    return plant
