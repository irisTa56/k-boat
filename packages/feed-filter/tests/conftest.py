"""Shared pytest fixtures and deterministic helpers.

Fixtures here are tmp_path-based and network-free; no real vault writes, no real
HTTP. Tests hang their fakes (a tmp vault via `state_dir`, MockTransport clients)
off this file.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from feed_filter import fetch
from kboat.lock import LOCK_DIR_ENV

# The home directory the suite started under, read before any test can move `HOME`.
_REAL_LOCK_ROOT = Path(os.path.expanduser("~")) / ".k-boat"


def _names_under(root: Path) -> frozenset[str]:
    return (
        frozenset(str(p.relative_to(root)) for p in root.rglob("*"))
        if root.is_dir()
        else frozenset()
    )


_REAL_LOCK_NAMES = _names_under(_REAL_LOCK_ROOT)


@pytest.fixture(autouse=True)
def isolate_lock_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Keep the shared vault lock every write takes out of the real home directory.

    ``kboat.lock`` puts its lock files under ``~/.k-boat`` unless ``KBOAT_LOCK_DIR``
    says otherwise, so without this every test that writes a feed note would leave one
    there. A directory of its own rather than ``tmp_path``, which some tests use as the
    vault. Set through a ``MonkeyPatch`` of its own rather than the test's, so a test
    calling ``monkeypatch.undo()`` mid-way does not undo it too. The check after the
    test is what would notice a test that reached the real ``~/.k-boat`` anyway; it
    compares names only, so a real run holding its own lock meanwhile does not fail it.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(LOCK_DIR_ENV, str(tmp_path_factory.mktemp("locks")))
        yield
    assert _names_under(_REAL_LOCK_ROOT) == _REAL_LOCK_NAMES, (
        f"a test created something under {_REAL_LOCK_ROOT}"
    )


@pytest.fixture(autouse=True)
def isolate_env_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test see the real ``OBSIDIAN_VAULT_PATH`` or ``EXA_API_KEY``.

    mise exports it (the iCloud vault) into the
    environment the test suite runs under, so without this a ``remind`` test
    would write feed notes into the real vault. Clear it by default; a test that
    needs a vault sets it to a tmp dir (``state_dir`` does), and ``vault_path()``
    otherwise raises, surfacing a test that forgot to.

    ``EXA_API_KEY`` is the same hazard with worse stakes: a suite started under a
    secret manager or from a shell that exported the live API secret would let a
    test reaching the real ``exa.search`` spend real credit and capture the key
    into an assertion or a failure dump. Clear it here so "a test never sees the real key" is a property of the
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
    """Redirect the DB, sites registry, and output vault into a tmp dir.

    The vault root is created, because in production it always exists — Obsidian
    made it, and a write that has to provision it is writing somewhere Obsidian
    does not read. `kboat.lock` therefore takes the vault's existence as a
    precondition, and a fixture that left it absent would test a path no run has.
    """
    monkeypatch.setenv("FEED_FILTER_DB", str(tmp_path / "feed-filter.db"))
    monkeypatch.setenv("FEED_FILTER_SITES", str(tmp_path / "sites.toml"))
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    yield tmp_path
