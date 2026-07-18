"""Shared pytest fixtures and deterministic helpers.

Fixtures here are tmp_path-based and network-free; no real Reminders, no real
HTTP. Modules added in later phases hang their fakes (rem runner, MockTransport
clients) off this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from feed_filter import fetch


@pytest.fixture(autouse=True)
def no_real_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize ``fetch``'s retry back-off so no test waits in real time.

    Retry-path tests (persistent ``503``/timeout) reach ``fetch`` through the
    gather pipelines without threading a ``sleep``, so each would otherwise sit
    through the 1+2+4s exponential back-off per failed feed/topic — a handful of
    such tests dominated the whole suite's wall clock. Patch the fetch-scoped
    ``_sleep`` seam to a no-op. Tests that inject their own ``sleep`` (the
    back-off timing tests in ``test_fetch``) are unaffected.
    """
    monkeypatch.setattr(fetch, "_sleep", lambda _seconds: None)


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
