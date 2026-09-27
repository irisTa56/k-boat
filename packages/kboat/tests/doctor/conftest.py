"""Shared vault fixtures for the doctor tests.

Fixtures rather than helpers imported across test modules, so the CLI tests and
the core tests share the setup without depending on each other.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from kboat.doctor import core
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
def evict(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path, str], Path]:
    """Make a named file one iCloud has evicted in place: there, under its own name.

    Only the file provider sets the dataless flag, so a test cannot. The probe is
    patched to answer for the files planted here, and the real one answers for
    every other file.
    """
    evicted: set[Path] = set()
    real = core._dataless
    monkeypatch.setattr(core, "_dataless", lambda path: path in evicted or real(path))

    def plant(directory: Path, name: str) -> Path:
        path = directory / name
        path.write_bytes(b"")
        evicted.add(path)
        return path

    return plant
