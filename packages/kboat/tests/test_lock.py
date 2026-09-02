"""Behavior tests for the vault lock.

Contention is exercised against a real lock file rather than a mocked one: the lock's
whole job is what two writers see on disk. A second acquisition inside the same process
stands in for a second process wherever that is faithful — an `flock` belongs to the
open file description, so two `vault_lock` calls in one process contend exactly as two
processes do. The one property that needs a real second process is the crash: only a
process that actually dies can show the kernel releasing its lock.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from kboat.lock import (
    DEFAULT_WAIT_S,
    LOCK_NAME,
    VaultLockedError,
    VaultLockUnavailableError,
    vault_lock,
)

# Long enough to observe, short enough not to pad the suite.
BRIEF = 0.1


def _lock_file(vault: Path) -> Path:
    return vault / LOCK_NAME


def _hold_from_another_fd(vault: Path) -> int:
    """Take the lock the way another process would, and return the fd holding it.

    A separate open file description, so `flock` treats it as a separate holder — the
    same contention a second process produces, without the cost of one.
    """
    fd = os.open(_lock_file(vault), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def _plant_record(vault: Path, payload: str) -> None:
    """Put `payload` in the lock file without holding the lock."""
    _lock_file(vault).write_text(payload, encoding="utf-8")


def test_holds_the_lock_for_the_block_and_releases_it_after(tmp_path: Path) -> None:
    with vault_lock(tmp_path) as lock_path:
        assert lock_path == _lock_file(tmp_path)
        record = json.loads(lock_path.read_text(encoding="utf-8"))
        assert record["pid"] == os.getpid()
        assert record["started"]
    # The file stays — an `flock` lives on the open description, not on the name, so
    # there is nothing to clean up and nothing to race over. What must be gone is the
    # hold itself.
    with vault_lock(tmp_path, wait_s=0.0):
        pass


def test_a_held_lock_is_refused_once_the_wait_expires_naming_the_holder(tmp_path: Path) -> None:
    fd = _hold_from_another_fd(tmp_path)
    # Only a real holder writes a record, so this one is planted to be read back.
    _plant_record(tmp_path, json.dumps({"pid": 4321, "started": "2026-07-26T00:00:00+00:00"}))
    try:
        started = time.monotonic()
        with pytest.raises(VaultLockedError) as excinfo, vault_lock(tmp_path, wait_s=BRIEF):
            pytest.fail("the lock was held; acquisition must not succeed")
        assert time.monotonic() - started >= BRIEF
    finally:
        os.close(fd)

    holder = excinfo.value.holder
    assert holder["pid"] == 4321
    assert holder["path"] == str(_lock_file(tmp_path))
    assert "4321" in str(excinfo.value)


def test_a_second_acquisition_is_refused_while_the_first_holds_it(tmp_path: Path) -> None:
    # The non-re-entrancy the module documents: `upsert` and `atomic_write_text` stay
    # lock-free because a writer that took the lock again would wait out its own hold.
    with vault_lock(tmp_path) as held:
        before = held.read_bytes()
        with pytest.raises(VaultLockedError), vault_lock(tmp_path, wait_s=BRIEF):
            pytest.fail("this process already holds it; acquisition must not succeed")
        # Byte-for-byte, not by pid: the refused acquisition is in this same process,
        # so a record it wrote would carry the same pid as the one it displaced.
        assert held.read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("{not json", id="not_json"),
        pytest.param("[]", id="not_a_mapping"),
        pytest.param('{"pid": "4321", "started": 7}', id="wrong_types"),
    ],
)
def test_an_unreadable_record_still_names_what_it_can(tmp_path: Path, content: str) -> None:
    # A reader can arrive between the lock being taken and the record being written, or
    # find whatever a crash truncated. A refusal has to survive that rather than fail
    # while assembling its own diagnostics.
    fd = _hold_from_another_fd(tmp_path)
    _plant_record(tmp_path, content)
    try:
        with pytest.raises(VaultLockedError) as excinfo, vault_lock(tmp_path, wait_s=0.0):
            pytest.fail("the lock was held; acquisition must not succeed")
    finally:
        os.close(fd)

    holder = excinfo.value.holder
    assert holder["pid"] is None
    assert holder["started"] is None
    assert "unidentified" in str(excinfo.value)


def test_the_refusal_record_has_one_shape_whatever_could_be_read(tmp_path: Path) -> None:
    # A CLI prints this dict verbatim, so its keys are part of the `locked` contract:
    # every one present, any of them null.
    fd = _hold_from_another_fd(tmp_path)
    _plant_record(tmp_path, "")
    try:
        with pytest.raises(VaultLockedError) as excinfo, vault_lock(tmp_path, wait_s=0.0):
            pytest.fail("the lock was held; acquisition must not succeed")
    finally:
        os.close(fd)

    holder = excinfo.value.holder
    assert sorted(holder) == ["path", "pid", "started"]
    assert holder["pid"] is None


def test_the_holder_record_is_replaced_rather_than_appended(tmp_path: Path) -> None:
    # The file outlives every hold, so a new holder that did not truncate would leave
    # the previous run's pid readable behind its own — and name the wrong process.
    with vault_lock(tmp_path):
        pass
    # Padded past anything the module writes, and padded with the *old pid* — a record
    # written over this without truncating would leave that tail readable, and would
    # not parse as JSON either. Whitespace padding would hide both.
    _plant_record(tmp_path, json.dumps({"pid": 999999, "started": "an old run"}) + " 999999" * 50)

    with vault_lock(tmp_path) as lock_path:
        text = lock_path.read_text(encoding="utf-8")
    assert json.loads(text)["pid"] == os.getpid()
    assert "999999" not in text


def test_the_lock_is_released_when_the_block_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"), vault_lock(tmp_path):
        raise RuntimeError("boom")
    with vault_lock(tmp_path, wait_s=0.0):
        pass


def test_a_waiting_acquisition_takes_the_lock_once_it_comes_free(tmp_path: Path) -> None:
    took_it = threading.Event()
    hold_on = threading.Event()

    def hold() -> None:
        with vault_lock(tmp_path):
            took_it.set()
            # The hold outlasts the clock reading below, so the wait it measures is one
            # this acquisition genuinely had to serve.
            hold_on.wait(timeout=5)
            time.sleep(BRIEF)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert took_it.wait(timeout=5), "the holder never took the lock"
        started = time.monotonic()
        hold_on.set()
        with vault_lock(tmp_path) as lock_path:
            waited = time.monotonic() - started
            assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        holder.join()

    # Waited rather than won a start race: `took_it` says the holder had the lock, so
    # this acquisition blocked, and the release it was blocked on cannot come before
    # `started` plus the holder's sleep. Both readings are this thread's own — the
    # release instant is not observable from out here, and a stamp taken in the holder
    # after its `with` block is a stamp taken after the release it claims to mark.
    assert waited >= BRIEF


def test_a_holder_that_dies_without_releasing_leaves_no_lock_behind(tmp_path: Path) -> None:
    # The property the whole design rests on, and the only one that needs a real second
    # process: the kernel drops an `flock` when the holding fd closes, however the
    # process ended. This is why there is no stale window, no age heuristic, no pid
    # liveness check and no takeover anywhere in the module.
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, os, sys\n"
                "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                'os.write(fd, b\'{"pid": 1, "started": "never released"}\\n\')\n'
                "os._exit(0)\n"  # no unwinding, no LOCK_UN, no close
            ),
            str(_lock_file(tmp_path)),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == 0, child.stderr.decode()
    assert "never released" in _lock_file(tmp_path).read_text(encoding="utf-8")

    # No wait at all: had the dead holder's lock survived, this would refuse.
    with vault_lock(tmp_path, wait_s=0.0) as lock_path:
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_a_missing_vault_root_is_reported_rather_than_created(tmp_path: Path) -> None:
    # Provisioning a vault is not the lock's business: a mis-typed `--vault` must fail
    # here rather than grow a second, empty vault beside the real one.
    vault = tmp_path / "typo-vault"
    with pytest.raises(VaultLockUnavailableError), vault_lock(vault):
        pytest.fail("there is no vault to lock; acquisition must not succeed")
    assert not vault.exists()


def test_a_lock_that_cannot_be_opened_is_named_rather_than_left_a_bare_oserror(
    tmp_path: Path,
) -> None:
    # A CLI has to tell "someone holds the vault" from "the lock does not work", and it
    # also runs subprocesses that raise `OSError` of their own — so this one carries its
    # own type while staying catchable as the `OSError` it is.
    (tmp_path / "Sources").mkdir()
    tmp_path.chmod(0o555)  # a vault root nothing can be created in
    try:
        with pytest.raises(VaultLockUnavailableError) as excinfo, vault_lock(tmp_path):
            pytest.fail("the lock could not be opened; acquisition must not succeed")
    finally:
        tmp_path.chmod(0o755)
    assert isinstance(excinfo.value, OSError), "an existing OSError handler must still catch it"
    assert "cannot take the vault lock" in str(excinfo.value)


def test_a_filesystem_that_refuses_to_lock_is_reported_not_treated_as_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `flock` answers contention with `EWOULDBLOCK`. Anything else — a filesystem that
    # does not implement it, a bad fd — is the mechanism failing, and waiting out a
    # deadline for it would end in a refusal naming a holder that does not exist.
    def boom(_fd: int, _op: int) -> None:
        raise OSError("no locks on this filesystem")

    monkeypatch.setattr(fcntl, "flock", boom)
    with pytest.raises(VaultLockUnavailableError), vault_lock(tmp_path):
        pytest.fail("the lock could not be taken; acquisition must not succeed")


def test_a_record_that_cannot_be_written_is_reported_rather_than_held_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Holding the vault while unable to say who holds it leaves every other run a
    # refusal it cannot explain, so the acquisition fails instead — and the lock does
    # not outlive it, since the fd closes on the way out.
    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "ftruncate", boom)
    with pytest.raises(VaultLockUnavailableError), vault_lock(tmp_path):
        pytest.fail("the record could not be written; acquisition must not succeed")

    monkeypatch.undo()
    with vault_lock(tmp_path, wait_s=0.0):
        pass


def test_the_shipped_wait_is_long_enough_to_cover_a_note_write(tmp_path: Path) -> None:
    # `DEFAULT_WAIT_S` is behaviour, not a tuning knob, and it is bounded from both
    # sides. Too low and an overlap with one note write costs a refused phase again —
    # the immediate refusal this policy replaced. Too high and the other half of the
    # policy goes: a `kboat-*` command a person runs would sit there instead of saying
    # who has the vault, which is what "wait a little" was weighed against.
    assert 1.0 <= DEFAULT_WAIT_S <= 15.0

    took_it = threading.Event()

    def hold() -> None:
        with vault_lock(tmp_path):
            took_it.set()
            time.sleep(BRIEF)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert took_it.wait(timeout=5), "the holder never took the lock"
        # No `wait_s`: the shipped default is what has to carry this.
        with vault_lock(tmp_path) as lock_path:
            assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        holder.join()


def test_a_release_whose_file_was_deleted_under_it_still_exits_cleanly(tmp_path: Path) -> None:
    # The lock lives on the descriptor, not the name, so someone clearing the vault by
    # hand mid-run must not turn a successful run into a failure on its way out. It does
    # cost exclusion — the next writer creates a new inode and locks that instead —
    # which is why nothing here deletes the file and nothing asks a human to.
    with vault_lock(tmp_path) as lock_path:
        lock_path.unlink()
    assert not _lock_file(tmp_path).exists()

    # And the lock is re-creatable afterwards rather than left in a broken state.
    with vault_lock(tmp_path, wait_s=0.0) as lock_path:
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_a_symlink_at_the_lock_name_is_refused_rather_than_written_through(
    tmp_path: Path,
) -> None:
    # Exclusion is an agreement about one inode, so a symlink at the lock's name would
    # put the lock on a file somewhere else — and, opened without care, let the module
    # truncate and overwrite whatever it points at. `O_NOFOLLOW` refuses it outright.
    outside = tmp_path / "not-the-lock"
    outside.write_text("someone else's file\n", encoding="utf-8")
    _lock_file(tmp_path).symlink_to(outside)

    with pytest.raises(VaultLockUnavailableError), vault_lock(tmp_path, wait_s=0.0):
        pytest.fail("the lock name is a symlink; acquisition must not succeed")
    assert outside.read_text(encoding="utf-8") == "someone else's file\n"


def test_the_refusal_reads_the_record_through_the_held_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The refusal reads the record from the fd it opened `O_NOFOLLOW`, not by re-opening
    # the name. Re-opening would drop that protection and describe whatever is at the
    # name by then — here a decoy planted while the waiter sleeps, which is what a
    # symlink swapped in mid-wait would do on a vault someone else can write to.
    fd = _hold_from_another_fd(tmp_path)
    _plant_record(tmp_path, json.dumps({"pid": 4321, "started": "the real holder"}))
    decoy = tmp_path / "decoy"
    decoy.write_text(json.dumps({"pid": 5555, "started": "not the holder"}), encoding="utf-8")

    real_sleep = time.sleep

    def swap_then_sleep(seconds: float) -> None:
        _lock_file(tmp_path).unlink()
        _lock_file(tmp_path).symlink_to(decoy)
        monkeypatch.setattr(time, "sleep", real_sleep)
        real_sleep(seconds)

    monkeypatch.setattr(time, "sleep", swap_then_sleep)
    try:
        with pytest.raises(VaultLockedError) as excinfo, vault_lock(tmp_path, wait_s=BRIEF):
            pytest.fail("the lock was held; acquisition must not succeed")
    finally:
        os.close(fd)

    assert excinfo.value.holder["pid"] == 4321, "the record came from the name, not the fd"


def test_a_child_process_does_not_inherit_the_hold(tmp_path: Path) -> None:
    # `kboat-repos refresh` runs `gh` under the hold. An inherited fd would keep the
    # lock alive for the life of the child, past the parent's release — silently, since
    # nothing else would look different.
    #
    # Spawned with `close_fds=False` on purpose: `subprocess`'s default cleanup would
    # hide an inheritable fd, and that default is the caller's business. What this pins
    # is the layer under it — the lock's own descriptor is close-on-exec.
    with vault_lock(tmp_path):
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
            close_fds=False,
        )
    try:
        assert child.poll() is None, "the child must still be alive for this to mean anything"
        with vault_lock(tmp_path, wait_s=0.0) as lock_path:
            assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        assert child.stdin is not None
        child.stdin.close()
        child.wait(timeout=30)


def test_a_supplied_wait_bounds_the_acquisition(tmp_path: Path) -> None:
    # `wait_s` is the one knob `vault_lock` keeps, because a timeout is what a caller of
    # a blocking acquire legitimately varies — but a parameter accepted and then ignored
    # would look identical from every other test, which all pass values *below* the
    # default. This is the one that would notice: it must return in its own time, not
    # the default's.
    fd = _hold_from_another_fd(tmp_path)
    try:
        started = time.monotonic()
        with pytest.raises(VaultLockedError), vault_lock(tmp_path, wait_s=BRIEF):
            pytest.fail("the lock was held; acquisition must not succeed")
        elapsed = time.monotonic() - started
    finally:
        os.close(fd)

    assert elapsed >= BRIEF
    assert elapsed < DEFAULT_WAIT_S, "the supplied wait was ignored in favour of the default"
