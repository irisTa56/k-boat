"""Shared pytest fixtures and deterministic helpers.

Fixtures here are tmp_path-based and network-free; no real vault writes, no real
HTTP. Tests hang their fakes (a tmp vault via `state_dir`, MockTransport clients)
off this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from feed_filter import fetch


@pytest.fixture(autouse=True)
def isolate_env_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test see the real ``OBSIDIAN_VAULT_PATH`` or ``EXA_API_KEY``.

    The workspace ``.env`` exports it (the iCloud vault) into the environment the
    test suite runs under, so without this a ``remind`` test would write feed
    notes into the real vault. Clear it by default; a test that needs a vault
    sets it to a tmp dir (``state_dir`` does), and ``vault_path()`` otherwise
    raises, surfacing a test that forgot to.

    ``EXA_API_KEY`` is the same hazard with worse stakes: the workspace ``.env``
    exports a live API secret, so a test reaching the real ``exa.search`` would
    spend real credit and could capture the key into an assertion or a failure
    dump. Clear it here so "a test never sees the real key" is a property of the
    harness; ``test_exa`` sets its own fake.
    """
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)


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
    """Redirect the DB, sites registry, and output vault into a tmp dir."""
    monkeypatch.setenv("FEED_FILTER_DB", str(tmp_path / "feed-filter.db"))
    monkeypatch.setenv("FEED_FILTER_SITES", str(tmp_path / "sites.toml"))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    yield tmp_path
