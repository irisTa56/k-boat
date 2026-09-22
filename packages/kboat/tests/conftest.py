"""Shared fixtures and helpers for the `kboat` suite."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import kboat.lock
from kboat.lock import LOCK_DIR_ENV, lock_file

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
    """Keep every vault lock a test takes out of the real home directory.

    A directory of its own rather than `tmp_path`, which many tests use as the vault
    and some make read-only. Set through a `MonkeyPatch` of its own rather than the
    test's, so a test calling `monkeypatch.undo()` mid-way does not undo it too. The
    check after the test is what would notice a test that reached the real `~/.k-boat`
    anyway; it compares names only, so a real run holding its own lock meanwhile does
    not fail it.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(LOCK_DIR_ENV, str(tmp_path_factory.mktemp("locks")))
        yield
    assert _names_under(_REAL_LOCK_ROOT) == _REAL_LOCK_NAMES, (
        f"a test created something under {_REAL_LOCK_ROOT}"
    )


@pytest.fixture
def lock_is_held() -> Callable[[Path], bool]:
    """A probe for whether anything holds the vault lock right now, from another fd.

    The lock file outlives every hold, so its existence says nothing — only trying to
    take it does. A separate open file description, so `flock` answers as it would for
    another process even when this is the one holding it.

    A fixture rather than an importable helper: a test module cannot import from
    `conftest` without putting the tests package on the type checker's path too.

    Do not call it from more than one thread of the same test — two concurrent probes
    see each other's momentary hold and both answer `True`.
    """

    def held(vault: Path) -> bool:
        fd = os.open(lock_file(vault), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    return held


@pytest.fixture
def brief_lock_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the vault lock's shipped wait, for a test that means to be refused.

    `vault_lock` takes a `wait_s`, but no CLI exposes one — that is the point of the
    single policy — so a test driving a CLI can only move the default. Without this,
    each test that holds the vault and asserts the refusal sits out the real wait, and
    together they turn a one-second suite into a thirty-second one. `vault_lock`
    resolves the default at call time, which is what makes it substitutable at all.

    Requested by name, never autouse: the test that pins the shipped value
    (`test_the_shipped_wait_is_long_enough_to_cover_a_note_write`) would be hollowed
    out by a global override.
    """
    monkeypatch.setattr(kboat.lock, "DEFAULT_WAIT_S", 0.05)
