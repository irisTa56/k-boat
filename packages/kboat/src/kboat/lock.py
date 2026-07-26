"""The vault lock — one mutating run at a time.

The vault is one shared database with several writers, all on one Mac: the daily
K-Boat routine, a feed-filter or forum run, and a human running a `kboat-*`
command or editing in Obsidian. Atomic writes (`kboat.io_utils`) make each file
land whole; they do not stop two runs from interleaving, where one reads a note,
the other rewrites it, and the first writes its own version back over the
rewrite. `vault_lock` closes that window between the runs that take it. The human
in Obsidian is outside it — Obsidian holds no lock — which
`kboat-vault-conventions` records along with the rest of what is outside.

The lock is `<vault>/.kboat.lock`, created with ``O_CREAT | O_EXCL`` — one atomic
syscall that succeeds for exactly one process — holding a JSON `{pid, started}`
record so a refusal can name who holds the vault. It is vault-wide rather than
per-note so that no writer has to know which folders another writer touches: a
K-Boat run rewrites many notes, and while feed-filter writes only `Feeds/`,
nothing but convention keeps that folder to one writer.

Inside the vault, and so inside iCloud's synced tree, because every writer is on
one Mac: the routine has to be local (the queue, the NotebookLM cookies, the
vault and the knowledge store all are), so the lock only ever arbitrates local
processes and a replicated copy is inert. A second Mac writing the same vault
would be a different problem — its lock would belong in a local state directory
keyed by the vault, since a synced `O_EXCL` create is not one atomic decision.

Two policies over the one mechanism, chosen by the caller through `wait_s`, and
the choice turns on what a refusal costs the caller: refuse at once (the default —
a K-Boat phase's work survives being deferred, since the dispositions, the
cooldown clock and the queue are all still on disk and every phase is idempotent),
or wait a bounded few seconds (feed-filter, whose unit is one entry per process,
so a refusal drops that entry until the next gather rediscovers it).

A holder that died without releasing would lock the vault forever, so a lock whose
holder is gone is taken over with a warning on stderr — decided by the pid its
record names, with `stale_after_s` covering only the moment that record is not yet
readable. `_take_over` argues for that split.

**What this module deliberately does not defend against.** Two writers can, in
principle, judge one leftover lock in the same instant and both clear it, and then
both write. Nothing here stops that, because on this deployment there is no third
writer, no adversarial one, and no writer on another host — so the whole cost of
that race is one lost note write, which the next daily run recomputes and redoes.
Machinery to close it (a second guard file, and its own recovery when a crash
leaves *that* behind) buys a deferred write and brings failure modes of its own.
What is here instead defends the cases whose cost is *not* a deferred write — a
killed run wedging the vault, an acquire that neither succeeds nor refuses, a
refusal naming the wrong process — plus one cheap enough not to argue over: a
release does not delete a lock that is no longer its own.

**Why a lock file and not `fcntl.flock`.** An advisory lock would need no stale
window and no takeover at all — the kernel drops it when the holder dies. It would
still be able to name its holder, since the flocked file can carry the same
record; that is not a reason to prefer `O_EXCL`. The reason is the file the lock
lives in: it sits under the iCloud file provider, where advisory-lock semantics are
not something this package can verify, whereas `O_CREAT | O_EXCL` is a create that
either happened or did not. That premise is an assumption, not a measurement, and
the stale window and takeover are what it costs; anyone who establishes how `flock`
behaves on a provider-backed path should read them as what that finding deletes.

**Acquire at the edge, never inside a writer.** The lock is not re-entrant, so a
second acquisition from inside the first would self-refuse (`wait_s = 0`) or wait
out its own hold (`wait_s > 0`). `upsert` and `atomic_write_text` therefore stay
lock-free, and a CLI takes the lock once around the whole run.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

LOCK_NAME = ".kboat.lock"
DEFAULT_STALE_AFTER_S = 3600.0
DEFAULT_POLL_INTERVAL_S = 0.1


class VaultLockedError(Exception):
    """Another process holds the vault lock, and this one may not write.

    `holder` is a JSON-safe record of who holds it, for a CLI to print as the
    `{status: "locked", holder}` refusal.
    """

    def __init__(self, holder: dict[str, object]) -> None:
        pid = holder.get("pid")
        started = holder.get("started")
        who = f"pid {pid}" if pid is not None else "an unidentified process"
        since = f" since {started}" if started else ""
        super().__init__(f"vault is locked by {who}{since}: {holder.get('path')}")
        self.holder = holder


class VaultLockUnavailableError(OSError):
    """The lock file could not be created at all, so nothing about the vault is known.

    Distinct from `VaultLockedError`, which says another run holds the vault and this
    one should come back later. This one says the mechanism itself did not work — a
    read-only vault root, a denied iCloud tree, a full disk — and no waiting fixes it.

    An `OSError` subclass, so a caller that already reports "the write failed" for an
    `OSError` keeps working; a caller that wants to tell the two apart, as the K-Boat
    CLIs do, catches this before the operations that raise ordinary `OSError`s of
    their own.
    """


def _read_holder(lock_path: Path) -> dict[str, object]:
    """The lock's holder record, as much of it as is readable.

    Every key is always present and every value may be `null`, so the `holder` a
    CLI prints has one shape whatever it could read. The file can be read in the
    window between its creation and the write of its record, it can hold whatever
    a crash truncated, and it can vanish under this read — and a refusal still has
    to say what it can rather than fail while assembling its own diagnostics.
    """
    record: dict[str, object] = {
        "pid": None,
        "started": None,
        "age_s": None,
        "path": str(lock_path),
    }
    try:
        loaded = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return record
    if isinstance(loaded, dict):
        pid = loaded.get("pid")
        started = loaded.get("started")
        record["pid"] = pid if isinstance(pid, int) else None
        record["started"] = started if isinstance(started, str) else None
    return record


class _Probe(NamedTuple):
    """How old the lock is, and which file that was — both from one `lstat`.

    They travel together because a removal is decided from the age and carried out
    on the name: reading them in separate calls is what lets the file change in
    between, so the unlink lands on a file nobody judged.
    """

    age_s: float
    identity: tuple[int, int]


def _probe(lock_path: Path) -> _Probe | None:
    """The lock's age and identity, or `None` if the name is gone.

    `lstat`, not `stat`: `O_CREAT | O_EXCL` fails for a name that exists whether or
    not it resolves, so a symlink with no target has to yield an age like any other
    lock. Following it would report the lock as gone while every claim went on
    failing, and the acquire loop would retry a state that never settles. Anything
    other than a missing name is raised rather than read as absence, for the same
    reason: a name we cannot judge is not a name we may remove.
    """
    try:
        st = os.lstat(lock_path)
    except FileNotFoundError:
        return None
    return _Probe(max(0.0, time.time() - st.st_mtime), (st.st_dev, st.st_ino))


def _unlink_if_ours(lock_path: Path, identity: tuple[int, int]) -> bool:
    """Remove the lock if it is still the file `identity` names, and say whether it did.

    All three removals here — a release on the way out, a takeover clearing a
    leftover, and the cleanup of a lock we could not finish writing — judge a file and
    then act on a name, and the name can be a different file by then. Cheap insurance
    rather than a guarantee the vault depends on: one `lstat`, and it keeps a run that
    was suspended long enough to be taken over (a slept laptop) from deleting, as it
    resumes and releases, the lock of the run that replaced it.

    Identity is `(st_dev, st_ino)`, which assumes the filesystem does not hand a
    freed inode straight back to the next file at the same name; APFS, which the
    vault is on, numbers them monotonically.

    An `OSError` is reported as "did not remove it" rather than raised: a release that
    raised would replace a successful run's outcome with a failure. The caller is what
    keeps that from becoming a silent loop — `_acquire` waits rather than retrying a
    takeover it could not carry out.
    """
    try:
        st = os.lstat(lock_path)
        if (st.st_dev, st.st_ino) != identity:
            return False
        lock_path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _claim(lock_path: Path) -> tuple[int, int] | None:
    """Create the lock with our `{pid, started}` record in it, exclusively.

    Returns the file's identity, or `None` when another process already holds it.
    Release compares that identity, so a lock taken over while we held it is never
    removed by us.
    """
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return None
    st = os.fstat(fd)
    identity = (st.st_dev, st.st_ino)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started": datetime.now(UTC).isoformat()}, f)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        # A lock we could not finish writing is one nobody can release.
        _unlink_if_ours(lock_path, identity)
        raise
    return identity


def _holder_is_alive(pid: int) -> bool:
    """Whether the recorded holder is still a process on this machine.

    Takes a pid `_read_holder` could read as an integer; the unreadable case belongs
    to `_is_leftover`, which decides it by age instead. A pid of zero or below is not
    a process either, and is refused before the call rather than passed on: `kill(0)`
    signals our own process group, and a negative pid signals a group by id — so a
    corrupt record must not reach `os.kill` at all.

    `PermissionError` counts as alive, since a process we may not signal is still a
    process.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_leftover(lock_path: Path, probe: _Probe, stale_after_s: float) -> bool:
    """Whether this lock was left behind by a run that is gone.

    **The pid decides; the age only covers the pid we cannot read.** A lock whose
    record names a process that no longer exists is a leftover whatever its mtime
    says, and clearing it at once is what keeps an interrupted run — a killed
    routine, a Ctrl-C at the keyboard — from making the vault unwritable until an
    hour has passed. Age is not a second opinion on that: it cannot tell a crashed
    holder from a *suspended* one (this vault lives on a laptop that sleeps, and an
    mtime keeps aging while the process sits frozen), so a live pid always wins over
    any age. What age is for is the one moment a held lock has no readable pid at
    all: between the `O_EXCL` create and the record landing, a few microseconds in
    which an unreadable record means "still being written", not "crashed".

    The cost is a pid recycled by an unrelated process, which makes a leftover look
    held. That one is reported rather than silently overwritten, with its age in the
    record for a human to act on.
    """
    pid = _read_holder(lock_path).get("pid")
    if not isinstance(pid, int):
        # No pid to judge, so only the age can say whether this is a crash or a
        # record still being written. `_read_holder` hands back an `int` or `None`;
        # the check is what narrows the type, not a second rule.
        return probe.age_s > stale_after_s
    return not _holder_is_alive(pid)


def _take_over(lock_path: Path, probe: _Probe) -> bool:
    """Clear the leftover lock `probe` judged, saying so on stderr.

    Only that file, and only if it is still there: anything that replaced it in the
    meantime — a human clearing the vault by hand, another run that got there first —
    belongs to whoever put it there. `False` says the lock is still in place, which is
    a lock nothing here can clear (a directory at that name, a denied parent), so the
    caller must wait or refuse rather than try again.
    """
    holder = _read_holder(lock_path)
    sys.stderr.write(
        f"warning: replacing a vault lock left by a run that is gone "
        f"({probe.age_s:.0f}s old, pid {holder.get('pid')}): {lock_path}\n"
    )
    return _unlink_if_ours(lock_path, probe.identity)


def _holder_now(lock_path: Path) -> dict[str, object]:
    """The holder record to refuse with, read as late as possible.

    A refusal is a diagnostic a human reads to tell a working run from a wedged one,
    so it has to name whoever holds the vault *now*. Read earlier in the acquire loop
    it could name a run that has since released, or a dead pid with an age of hours —
    which is exactly the reading the docs teach as "look at this".
    """
    holder = _read_holder(lock_path)
    probe = _probe(lock_path)
    # Null when the lock went away under this read, which is the same shape
    # `_read_holder` starts from — every refusal carries the same four keys.
    holder["age_s"] = round(probe.age_s, 1) if probe is not None else None
    return holder


def _acquire(
    lock_path: Path, *, stale_after_s: float, wait_s: float, poll_interval_s: float
) -> tuple[int, int]:
    """Hold the lock, or raise `VaultLockedError` once the wait has passed."""
    deadline = time.monotonic() + wait_s
    may_retry_free_name = True
    while True:
        identity = _claim(lock_path)
        if identity is not None:
            return identity
        probe = _probe(lock_path)
        if probe is None:
            # Released between our failed create and our look at the name. Claim it at
            # once, which is what lets a zero-wait caller take a lock freed under it —
            # but only once, so a name that keeps coming and going cannot spin here.
            if may_retry_free_name:
                may_retry_free_name = False
                continue
        elif _is_leftover(lock_path, probe, stale_after_s) and _take_over(lock_path, probe):
            # Cleared, so claiming it is the point of having done so. A takeover that
            # could *not* clear it falls through instead: retrying would spin on a
            # lock nothing here can remove, and waiting or refusing tells the caller.
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VaultLockedError(_holder_now(lock_path))
        time.sleep(min(poll_interval_s, remaining))


@contextmanager
def vault_lock(
    vault: Path,
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    wait_s: float = 0.0,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> Iterator[Path]:
    """Hold the vault lock for the duration of the block, and release it on the way out.

    Raises `VaultLockedError` when another process holds it and `wait_s` (default: no
    wait at all) passes without it coming free, and `VaultLockUnavailableError` when
    the lock file could not be created at all. The release runs on every exit path,
    an exception included — the one property here whose failure is not a deferred
    write but a vault wedged until the takeover clears it.

    `vault` must already exist. Provisioning it is not the lock's business, and a
    mis-typed `--vault` or a misresolved `$OBSIDIAN_VAULT_PATH` should fail here
    rather than grow a second, empty vault — as a `VaultLockUnavailableError`, since
    a vault that is not there is a vault whose lock cannot be taken.

    `stale_after_s` must stay well above the time it takes to create a lock and write
    its record. That window is the one moment a held lock has no readable pid, so a
    would-be taker judges its holder absent and the age is all that refuses it; a
    value near zero therefore turns a fresh lock into a leftover. The default is an
    hour against a window of microseconds, and no caller has a reason to narrow it —
    the parameter exists so a test can, and a test knows what it stands in for.
    """
    lock_path = vault / LOCK_NAME
    try:
        identity = _acquire(
            lock_path, stale_after_s=stale_after_s, wait_s=wait_s, poll_interval_s=poll_interval_s
        )
    except OSError as exc:
        # Named here rather than left as a bare `OSError`, so a caller can tell an
        # unusable lock from the `OSError`s its own work raises — a CLI that also
        # runs `gh` would otherwise report a missing binary as a vault problem.
        raise VaultLockUnavailableError(
            exc.errno, f"cannot take the vault lock: {exc}", str(lock_path)
        ) from exc
    try:
        yield lock_path
    finally:
        _unlink_if_ours(lock_path, identity)
