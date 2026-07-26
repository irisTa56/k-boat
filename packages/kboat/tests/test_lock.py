"""Behavior tests for the vault lock.

Contention is exercised against a real lock file rather than a mocked one: the
lock's whole job is what two processes see on disk, and `O_CREAT | O_EXCL` is
the only thing enforcing it. A second acquisition inside the same process stands
in for a second process — nothing in the mechanism is per-process, so the file
refuses both the same way.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from kboat.lock import (
    LOCK_NAME,
    VaultLockedError,
    VaultLockUnavailableError,
    vault_lock,
)


def _lock_file(vault: Path) -> Path:
    return vault / LOCK_NAME


# A pid no process can have: `pid_t` is 32-bit, while both platforms cap live pids
# far below this (Linux `pid_max` defaults to 4194304), so `os.kill` always answers
# "no such process". A plausible-looking number like 4321 would make every
# takeover test depend on that pid happening to be free on the machine running it.
DEAD_PID = 2**31 - 1


def _write_lock(vault: Path, *, pid: int | None = None, age_s: float = 0.0) -> Path:
    """Plant a lock. By default its holder is *this* process, so it is genuinely held.

    Pass `pid=DEAD_PID` for a lock whose holder is gone — a leftover, whatever its
    age. Which of the two a test wants is the whole distinction the takeover turns
    on, so neither is the quiet default: the live one is, because that is the state
    the lock spends its time in.
    """
    path = _lock_file(vault)
    record = {"pid": os.getpid() if pid is None else pid, "started": "2026-07-26T00:00:00+00:00"}
    path.write_text(json.dumps(record) + "\n")
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


def test_holds_the_lock_for_the_block_and_releases_it_after(tmp_path: Path) -> None:
    with vault_lock(tmp_path) as lock_path:
        assert lock_path == _lock_file(tmp_path)
        assert lock_path.exists()
        record = json.loads(lock_path.read_text(encoding="utf-8"))
        assert record["pid"] == os.getpid()
        assert record["started"]
    assert not _lock_file(tmp_path).exists()


def test_a_missing_vault_root_is_reported_rather_than_created(tmp_path: Path) -> None:
    # Provisioning a vault is not the lock's business: a mis-typed `--vault` must
    # fail here rather than grow a second, empty vault beside the real one.
    vault = tmp_path / "typo-vault"
    with pytest.raises(VaultLockUnavailableError), vault_lock(vault):
        pytest.fail("there is no vault to lock; acquisition must not succeed")
    assert not vault.exists()


def test_a_held_lock_is_refused_at_once_naming_the_holder(tmp_path: Path) -> None:
    _write_lock(tmp_path)
    with pytest.raises(VaultLockedError) as excinfo, vault_lock(tmp_path):
        pytest.fail("the lock was held; acquisition must not succeed")
    holder = excinfo.value.holder
    assert holder["pid"] == os.getpid()
    assert holder["path"] == str(_lock_file(tmp_path))
    assert str(os.getpid()) in str(excinfo.value)


def test_a_second_acquisition_is_refused_while_the_first_holds_it(tmp_path: Path) -> None:
    with vault_lock(tmp_path) as held:
        with pytest.raises(VaultLockedError), vault_lock(tmp_path):
            pytest.fail("the lock was held; acquisition must not succeed")
        # The refusal must not have removed the holder's lock on its way out.
        assert held.exists()
    assert not _lock_file(tmp_path).exists()


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="truncated"),
        pytest.param("{not json", id="not_json"),
        pytest.param("[]", id="not_a_mapping"),
        pytest.param('{"pid": "4321", "started": 7}', id="wrong_types"),
    ],
)
def test_an_unreadable_lock_record_still_names_what_it_can(tmp_path: Path, content: str) -> None:
    # A process that crashed between creating the file and writing its record
    # leaves a held lock with nothing readable inside, and the refusal has to
    # survive whatever is there rather than fail on its own diagnostics.
    _lock_file(tmp_path).write_text(content, encoding="utf-8")
    with pytest.raises(VaultLockedError) as excinfo, vault_lock(tmp_path):
        pytest.fail("the lock was held; acquisition must not succeed")
    holder = excinfo.value.holder
    assert holder["pid"] is None
    assert holder["started"] is None
    assert "unidentified" in str(excinfo.value)


@pytest.mark.parametrize("age_s", [5, 7200])
def test_a_lock_whose_holder_is_gone_is_taken_over_whatever_its_age(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], age_s: float
) -> None:
    # The rule the takeover turns on: the pid decides. Five seconds old is an
    # interrupted run the user is about to re-run — making it wait out the stale
    # window would take the vault offline for an hour over a crash the record already
    # shows. Two hours old is the same leftover, later; the age changes nothing.
    _write_lock(tmp_path, pid=DEAD_PID, age_s=age_s)
    with vault_lock(tmp_path, stale_after_s=3600) as lock_path:
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    err = capsys.readouterr().err
    assert "left by a run that is gone" in err
    assert str(DEAD_PID) in err
    assert not _lock_file(tmp_path).exists()


def test_the_lock_is_released_when_the_block_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"), vault_lock(tmp_path):
        raise RuntimeError("boom")
    assert not _lock_file(tmp_path).exists()


def test_release_leaves_a_lock_another_run_took_over(tmp_path: Path) -> None:
    # Declared stale and taken over while we held it, the file on disk is now
    # the other run's lock — ours to leave alone, or two runs would believe they
    # have the vault to themselves.
    with vault_lock(tmp_path) as lock_path:
        # A hard link keeps our inode allocated while the replacement is created,
        # so the replacement cannot land on it. Without that the test would rest
        # on the filesystem's reuse policy: APFS never reuses a freed inode, ext4
        # hands back the lowest free one in the group — the one just released.
        keepalive = tmp_path / "ours.keep"
        os.link(lock_path, keepalive)
        lock_path.unlink()
        _write_lock(tmp_path, pid=999)
        assert os.lstat(_lock_file(tmp_path)).st_ino != os.lstat(keepalive).st_ino
    assert json.loads(_lock_file(tmp_path).read_text(encoding="utf-8"))["pid"] == 999


def test_a_waiting_acquisition_gives_up_when_the_wait_expires(tmp_path: Path) -> None:
    _write_lock(tmp_path)  # held by a running process, so no takeover intervenes
    started = time.monotonic()
    with pytest.raises(VaultLockedError), vault_lock(tmp_path, wait_s=0.2, poll_interval_s=0.02):
        pytest.fail("the lock was held throughout; acquisition must not succeed")
    assert time.monotonic() - started >= 0.2


def test_a_waiting_acquisition_takes_the_lock_once_it_comes_free(tmp_path: Path) -> None:
    released = threading.Event()

    def hold() -> None:
        with vault_lock(tmp_path):
            time.sleep(0.1)
        released.set()

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        time.sleep(0.02)  # let the thread take the lock first
        with vault_lock(tmp_path, wait_s=5.0, poll_interval_s=0.02) as lock_path:
            assert released.is_set()
            assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        holder.join()
    assert not _lock_file(tmp_path).exists()


def test_a_lock_that_cannot_be_written_is_not_left_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(OSError, match="disk full"), vault_lock(tmp_path):
        pytest.fail("the lock could not be written; acquisition must not succeed")
    assert not _lock_file(tmp_path).exists()


def test_a_lock_released_under_the_stat_is_retried_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The holder released between our failed create and our look at the file:
    # there is nobody to wait for, so the next attempt takes it rather than
    # refusing on a lock that is already gone. The release is staged just before the
    # probe, which is the step between the failed create and the age check.
    from kboat import lock as lock_mod

    _write_lock(tmp_path)
    real_probe = lock_mod._probe

    def release_then_probe(path: Path) -> object:
        monkeypatch.setattr(lock_mod, "_probe", real_probe)
        path.unlink()
        return real_probe(path)

    monkeypatch.setattr(lock_mod, "_probe", release_then_probe)
    with vault_lock(tmp_path) as lock_path:
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_release_tolerates_a_lock_file_that_is_already_gone(tmp_path: Path) -> None:
    # Someone cleaned the vault under a running job. The release has nothing to
    # remove, and must leave the block's exit alone rather than raise on it.
    with vault_lock(tmp_path) as lock_path:
        lock_path.unlink()
    assert not _lock_file(tmp_path).exists()


def test_a_lock_name_with_no_readable_target_ages_and_is_taken_over(tmp_path: Path) -> None:
    # `O_CREAT | O_EXCL` fails for a name that exists whether or not it resolves,
    # so a symlink with no target has to age like any other lock. Read as absent
    # it would be a claim that never succeeds against a lock that never ages.
    dangling = _lock_file(tmp_path)
    dangling.symlink_to(tmp_path / "nowhere")
    # Backdated rather than judged against a zero window: `stale_after_s` has to stay
    # above the time a claim takes to write its record, so zero is not a value this
    # suite should present as usable.
    old = time.time() - 7200
    os.utime(dangling, (old, old), follow_symlinks=False)

    with vault_lock(tmp_path, stale_after_s=3600) as lock_path:
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert not _lock_file(tmp_path).exists()


def test_a_lock_name_with_no_readable_target_is_refused_while_it_is_fresh(
    tmp_path: Path,
) -> None:
    # The other half: it is refused on the deadline like any fresh holder, rather
    # than retried until the process is killed.
    _lock_file(tmp_path).symlink_to(tmp_path / "nowhere")
    with pytest.raises(VaultLockedError), vault_lock(tmp_path, wait_s=0.1, poll_interval_s=0.02):
        pytest.fail("the name was fresh; acquisition must not succeed")


def test_the_default_stale_window_is_an_hour_for_a_record_with_no_pid(tmp_path: Path) -> None:
    # The documented default, exercised as a default, on the only case it governs:
    # a record with no readable pid. Half an hour old, that is still a claim writing
    # its record; two hours old, it is a crash.
    def plant(age_s: float) -> None:
        path = _lock_file(tmp_path)
        path.write_text("", encoding="utf-8")
        old = time.time() - age_s
        os.utime(path, (old, old))

    plant(1800)
    with pytest.raises(VaultLockedError), vault_lock(tmp_path):
        pytest.fail("a half-hour-old lock is held; acquisition must not succeed")

    plant(7200)
    with vault_lock(tmp_path) as lock_path:
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()


@pytest.mark.parametrize(
    ("plant", "expect_pid"),
    [
        pytest.param(
            lambda p: p.write_text(f'{{"pid": {os.getpid()}, "started": "t"}}\n'),
            os.getpid(),
            id="readable",
        ),
        pytest.param(lambda p: p.write_text("{truncated"), None, id="unreadable"),
    ],
)
def test_the_holder_record_has_one_shape_whatever_could_be_read(
    tmp_path: Path, plant: object, expect_pid: int | None
) -> None:
    # A CLI prints this dict verbatim, so its keys are part of the `locked`
    # contract: every one present, any of them null. A key that appeared only when
    # the lock happened to be readable would make the record's shape a guess.
    plant(_lock_file(tmp_path))  # ty: ignore[call-non-callable]
    with pytest.raises(VaultLockedError) as excinfo, vault_lock(tmp_path):
        pytest.fail("the lock was held; acquisition must not succeed")
    holder = excinfo.value.holder
    assert sorted(holder) == ["age_s", "path", "pid", "started"]
    assert holder["pid"] == expect_pid
    assert isinstance(holder["age_s"], float)


def test_an_old_lock_whose_holder_is_still_running_is_not_taken_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Age alone cannot tell a crashed holder from a suspended one, and this vault
    # lives on a laptop that sleeps: mtime keeps advancing while the process sits
    # frozen. Taking such a lock over would hand the vault to a second writer and
    # produce the interleaving the lock exists to prevent, so the holder's pid must
    # be gone too. Our own pid stands in for a holder that is demonstrably alive.
    _write_lock(tmp_path, pid=os.getpid(), age_s=7200)
    with pytest.raises(VaultLockedError) as excinfo, vault_lock(tmp_path, stale_after_s=3600):
        pytest.fail("the holder is still running; acquisition must not succeed")

    holder = excinfo.value.holder
    assert holder["pid"] == os.getpid()
    # The age is reported, so a lock held for hours is visible to act on rather
    # than silently overwritten — the cost of requiring liveness.
    assert isinstance(holder["age_s"], float)
    assert holder["age_s"] >= 7200
    assert "left by a run that is gone" not in capsys.readouterr().err
    assert _lock_file(tmp_path).exists()


def test_an_old_lock_with_no_readable_pid_is_taken_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The crash the liveness check must not block on: a holder that died between
    # creating its lock and writing its record leaves no pid to ask about, and that
    # lock still has to be recoverable.
    lock_path = _lock_file(tmp_path)
    lock_path.write_text("", encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock_path, (old, old))

    with vault_lock(tmp_path, stale_after_s=3600) as held:
        assert json.loads(held.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert "left by a run that is gone" in capsys.readouterr().err


def test_a_holder_we_may_not_signal_counts_as_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pid owned by another user answers `EPERM`, not "no such process": there is
    # a process there, we simply may not poke it. Reading that as "gone" would take
    # over a live holder's lock, which is the one thing the liveness check is for.
    def not_ours(_pid: int, _sig: int) -> None:
        raise PermissionError("operation not permitted")

    _write_lock(tmp_path, pid=4321, age_s=7200)
    monkeypatch.setattr(os, "kill", not_ours)
    with pytest.raises(VaultLockedError), vault_lock(tmp_path, stale_after_s=3600):
        pytest.fail("a process holds the lock; acquisition must not succeed")
    assert _lock_file(tmp_path).exists()


def test_a_lock_replaced_under_a_removal_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The rule both removals go through. The case worth having it for: a run
    # suspended long enough to be taken over resumes and releases — by name, it would
    # delete the lock of the run that replaced it, leaving the vault unprotected
    # while that run writes.
    from kboat import lock as lock_mod

    _write_lock(tmp_path, pid=DEAD_PID)
    lock_path = _lock_file(tmp_path)
    probe = lock_mod._probe(lock_path)
    assert probe is not None
    # A hard link keeps the judged inode allocated, so the replacement cannot land on
    # it: without that the test would rest on the filesystem's reuse policy, and ext4
    # hands back the inode just freed.
    keepalive = tmp_path / "ours.keep"
    os.link(lock_path, keepalive)
    lock_path.unlink()
    _write_lock(tmp_path, pid=999)
    assert os.lstat(lock_path).st_ino != os.lstat(keepalive).st_ino

    lock_mod._unlink_if_ours(lock_path, probe.identity)

    assert lock_path.exists(), "the replacement belongs to whoever wrote it"
    assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == 999


def test_a_lock_that_cannot_be_created_is_named_rather_than_left_a_bare_oserror(
    tmp_path: Path,
) -> None:
    # A CLI has to tell "someone holds the vault" from "the lock does not work",
    # and it also runs subprocesses that raise `OSError` of their own — so this one
    # carries its own type rather than arriving as a bare `OSError`.
    (tmp_path / "Sources").mkdir()
    tmp_path.chmod(0o555)  # a vault root nothing can be created in
    try:
        with pytest.raises(VaultLockUnavailableError) as excinfo, vault_lock(tmp_path):
            pytest.fail("the lock could not be created; acquisition must not succeed")
    finally:
        tmp_path.chmod(0o755)
    assert isinstance(excinfo.value, OSError), "an existing OSError handler must still catch it"
    assert "cannot take the vault lock" in str(excinfo.value)


def test_a_lock_that_cannot_be_removed_is_refused_rather_than_retried_forever(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A leftover the takeover judges but cannot clear — here a directory at the lock's
    # name, which `O_EXCL` refuses to create over and `unlink` refuses to remove. Left
    # to retry, the acquire loop would neither claim, nor wait, nor refuse: it would
    # spin at CPU speed writing the takeover warning, which in the unattended routine
    # is a hung phase.
    stuck = _lock_file(tmp_path)
    stuck.mkdir()
    old = time.time() - 7200
    os.utime(stuck, (old, old))

    started = time.monotonic()
    with pytest.raises(VaultLockedError), vault_lock(tmp_path, stale_after_s=3600):
        pytest.fail("the lock could not be cleared; acquisition must not succeed")
    assert time.monotonic() - started < 3.0
    assert stuck.is_dir(), "nothing here can remove it, and it must not be mangled"
    assert "left by a run that is gone" in capsys.readouterr().err


def test_the_refusal_record_has_one_shape_with_no_lock_left_to_measure(tmp_path: Path) -> None:
    # The lock can go away between the decision to refuse and the read that describes
    # it. `age_s` is then null rather than missing, so a caller reads the same four
    # keys on every refusal.
    from kboat import lock as lock_mod

    holder = lock_mod._holder_now(_lock_file(tmp_path))
    assert sorted(holder) == ["age_s", "path", "pid", "started"]
    assert holder["age_s"] is None
    assert holder["pid"] is None


@pytest.mark.parametrize("pid", [0, -1])
def test_a_lock_naming_a_pid_that_is_not_a_process_is_a_leftover(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], pid: int
) -> None:
    # A record can be corrupt or hand-edited. Zero and negative are not processes but
    # `kill` reads them as process *groups* — zero being our own — so they are refused
    # before the call rather than asked about, and the lock counts as left behind.
    _write_lock(tmp_path, pid=pid, age_s=5)
    with vault_lock(tmp_path, stale_after_s=3600) as lock_path:
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert "left by a run that is gone" in capsys.readouterr().err


def test_a_name_that_keeps_vanishing_is_refused_after_one_immediate_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A claim that keeps failing against a name that keeps reading as gone: nobody to
    # wait for and no lock to take over. The immediate retry is what lets a zero-wait
    # caller take a lock freed under it, so it happens once — a second one would spin,
    # and a spinning acquire neither writes, refuses, nor exits.
    from kboat import lock as lock_mod

    claims = 0

    def never_claims(_p: Path) -> None:
        nonlocal claims
        claims += 1
        return None

    monkeypatch.setattr(lock_mod, "_claim", never_claims)
    monkeypatch.setattr(lock_mod, "_probe", lambda _p: None)
    started = time.monotonic()
    with pytest.raises(VaultLockedError), vault_lock(tmp_path):
        pytest.fail("the name never settled; acquisition must not succeed")
    assert time.monotonic() - started < 3.0
    assert claims == 2, "one claim, then one immediate retry, then the refusal"
