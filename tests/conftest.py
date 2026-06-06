"""Shared pytest fixtures and deterministic helpers.

Fixtures here are tmp_path-based and network-free; no real Reminders, no real
HTTP. Modules added in later phases hang their fakes (rem runner, MockTransport
clients) off this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove feed-filter env overrides so path resolution hits its defaults."""
    for var in ("FEED_FILTER_DB", "FEED_FILTER_SITES"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect the DB and sites registry into a tmp dir via env overrides."""
    monkeypatch.setenv("FEED_FILTER_DB", str(tmp_path / "feed-filter.db"))
    monkeypatch.setenv("FEED_FILTER_SITES", str(tmp_path / "sites.toml"))
    yield tmp_path
