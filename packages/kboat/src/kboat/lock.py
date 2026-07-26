"""The vault lock — one mutating run at a time.

The vault is one shared database with several writers, all on one Mac: the daily
K-Boat routine, a feed-filter or forum run, and a human running a `kboat-*`
command or editing in Obsidian. Atomic writes (`kboat.io_utils`) make each file
land whole; they do not stop two runs from interleaving, where one reads a note,
the other rewrites it, and the first writes its own version back over the
rewrite. `vault_lock` closes that window between the runs that take it. The human
in Obsidian is outside it — Obsidian holds no lock — which
`kboat-vault-conventions` records along with the rest of what is outside.

The lock is an advisory `flock` on `<vault>/.kboat.lock`. The file also holds a
JSON `{pid, started}` record, rewritten by each holder, so a refusal can name who
has the vault; advisory locking does not prevent reading, so a waiter reads that
record while the holder works. The record is a diagnostic and nothing more — no
decision here is made from it.

**The kernel releases the lock, which is why there is no stale state to recover.**
An `flock` belongs to the open file description, so it goes when the fd closes,
including on a `SIGKILL`, an OOM kill, or a panic. A crashed run therefore leaves
no lock behind, and this module needs no stale window, no age heuristic, no
liveness check on a recorded pid, and no takeover — the failure all of that would
exist to recover from cannot happen.

**The premise that buys it: all contention is same-host on a local volume**, which
`kboat-vault-conventions` states as the design's one invalidating assumption. What was
measured, since a reader here will want it: `~/Library/Mobile Documents/…` is a local
APFS directory behind a file-provider sync extension rather than a network mount, and
against this vault a second process was denied while the first held the lock, granted
once it released, and granted after it was killed without releasing. `fcntl` is
POSIX-only, which this package already is.

**The file is never deleted, and that is load-bearing.** An `flock` excludes only
writers holding the *same inode*, so two things protect that inode. The file is
opened `O_NOFOLLOW`, since a symlink at the name would put the lock on a file
outside the vault. And once created it stays: no writer unlinks it, and there is
nothing to clean up, because a crashed run leaves no lock to clear.

Anything that replaces that inode mid-run therefore gives two holders, since the next
writer locks the new one. A human deleting the file is the case to guard against, and
nothing here or in the docs asks anyone to — the difference from a design with a stale
window to clear by hand. A release whose file was deleted under it still exits cleanly;
the lock lives on the descriptor, not the name.

One replacement path is *not* ruled out. The vault sits behind a file provider, and
the verification above covers one session — not whether the provider can evict and
re-materialize `.kboat.lock` with a new inode over days. If it can, two runs could
overlap with nothing to show for it, and the lock would belong in a local, unsynced
directory keyed by the vault instead.

Its fd is close-on-exec, which Python has set by default since PEP 446. That matters
here rather than being incidental: `kboat-repos refresh` runs `gh` under the hold, and
an fd the child kept would hold the lock past the parent's release for as long as that
child lived. It is `exec` this covers — a `fork`ed child that never execs keeps the
open file description, and so the lock; nothing here forks.

**One policy: wait a little, then refuse.** Every caller gets `DEFAULT_WAIT_S` and
then a `VaultLockedError` naming the holder — no per-caller variation, and no
attended/unattended split. `kboat-vault-conventions` argues for it; the shape it
constrains here is that the wait covers a hold measured in note writes and not the
long ones, so `wait_s` is a bound and never a guarantee of acquiring.

**Acquire at the edge, never inside a writer.** The lock is not re-entrant — an
`flock` is per open file description, so a second acquisition from inside the
first opens a second description and waits out its own hold. `upsert` and
`atomic_write_text` therefore stay lock-free, and a CLI takes the lock once around
the whole run.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

LOCK_NAME = ".kboat.lock"
# How long an acquisition waits before refusing. Long enough to cover overlapping a
# note write, short enough that a person does not notice having waited.
DEFAULT_WAIT_S = 5.0
# How often a waiter re-tries the `flock`. Private: no caller has a reason to
# choose it, and a knob nothing turns is one more thing to keep true.
_POLL_INTERVAL_S = 0.1


class VaultLockedError(Exception):
    """Another process holds the vault lock, and the wait for it expired.

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
    """The lock could not be operated at all, so nothing about the vault is known.

    Distinct from `VaultLockedError`, which says another run holds the vault and this
    one should come back later. This one says the mechanism itself did not work — a
    vault root that is not there, a denied iCloud tree, a filesystem that will not
    take an `flock` — and no waiting fixes it.

    An `OSError` subclass, so a caller that already reports "the write failed" for an
    `OSError` keeps working; a caller that wants to tell the two apart, as the K-Boat
    CLIs do, catches this before the operations that raise ordinary `OSError`s of
    their own.
    """


def _read_holder(fd: int, lock_path: Path) -> dict[str, object]:
    """Who holds the lock, as much of it as is readable.

    Every key is always present and every value may be `null`, so the `holder` a CLI
    prints has one shape whatever it could read: the file can hold whatever a crash
    truncated, and it can be unreadable, and a refusal still has to say what it can
    rather than fail while assembling its own diagnostics.

    One inaccuracy is accepted, since the record decides nothing: the file outlives
    every hold, so a read landing in the microseconds between a new holder taking the
    lock and rewriting the record returns the *previous* holder's record, naming a
    process that has already finished.

    Read through `fd` rather than by name, since the caller already holds one on the
    inode the lock arbitrates. That is the inode whose record is wanted, it still reads
    after the file is unlinked under the run, and it cannot be redirected — re-opening
    the name would drop the `O_NOFOLLOW` the lock itself was opened with, so a symlink
    planted in between would put another file's contents on stdout as the holder.
    `lock_path` is only for the record's `path` field.
    """
    record: dict[str, object] = {"pid": None, "started": None, "path": str(lock_path)}
    try:
        # Bounded: a record is well under this, and a lock file that somehow is not is
        # not one to pull into memory whole.
        loaded = json.loads(os.pread(fd, 4096, 0).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return record
    if isinstance(loaded, dict):
        pid = loaded.get("pid")
        started = loaded.get("started")
        record["pid"] = pid if isinstance(pid, int) else None
        record["started"] = started if isinstance(started, str) else None
    return record


def _wait_for_lock(fd: int, lock_path: Path, *, wait_s: float) -> None:
    """Take the `flock`, or raise `VaultLockedError` once the wait has passed.

    Polled with `LOCK_NB` rather than blocking on `LOCK_EX`, because a blocking
    `flock` can only be bounded with a signal, and a library has no business
    installing one in its caller's process.
    """
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            # Held by someone else. Anything other than "would block" is the mechanism
            # failing rather than contention, and is left for the caller to rename.
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VaultLockedError(_read_holder(fd, lock_path))
        time.sleep(min(_POLL_INTERVAL_S, remaining))


def _write_record(fd: int) -> None:
    """Replace the lock file's contents with our `{pid, started}` record.

    Truncated first so the previous holder's record cannot be left trailing behind a
    shorter one — the file outlives every hold, so there is always a previous record to
    displace. A reader can still catch the old one whole in the window before this
    runs; `_read_holder` accepts that.

    Written at offset zero without seeking, because nothing here ever moves the fd's
    offset: it is freshly opened, `ftruncate` does not move it, and `_read_holder`
    reads with `pread`.

    Not `fsync`ed: the only reader is another live process on this host, which sees the
    write through the page cache, and nothing reads this record after a crash — a crash
    releases the lock rather than leaving one to explain.
    """
    os.ftruncate(fd, 0)
    payload = json.dumps({"pid": os.getpid(), "started": datetime.now(UTC).isoformat()}) + "\n"
    os.write(fd, payload.encode("utf-8"))


def _unavailable(lock_path: Path, exc: OSError) -> VaultLockUnavailableError:
    """Rename an `OSError` from the lock itself, so a caller can tell it apart.

    A CLI that also shells out to `gh` would otherwise report a missing binary as a
    vault problem, and one whose whole output is a JSON report would answer an
    unwritable vault root with a traceback and an empty stdout.
    """
    return VaultLockUnavailableError(
        exc.errno, f"cannot take the vault lock: {exc}", str(lock_path)
    )


@contextmanager
def vault_lock(vault: Path, *, wait_s: float | None = None) -> Iterator[Path]:
    """Hold the vault lock for the duration of the block, and release it on the way out.

    Raises `VaultLockedError` when another process holds it and `wait_s` (default:
    `DEFAULT_WAIT_S`) passes without it coming free, and `VaultLockUnavailableError`
    when the lock cannot be operated at all.

    The release runs on every exit path, an exception included: closing the fd is what
    releases, so even a path that never reaches this `finally` — the process dying —
    frees it.
    `wait_s` is resolved here rather than in the signature so the shipped default is one
    value, and one a test can stand in for. It stays a parameter where `poll_interval_s`
    did not, because a timeout is the thing a caller of a blocking acquire legitimately
    varies — no CLI exposes one today, which is what makes the policy single, but the
    knob is the library's to offer rather than an internal detail.

    `vault` must already exist. Provisioning it is not the lock's business, and a
    mis-typed `--vault` or a misresolved `$OBSIDIAN_VAULT_PATH` should fail here rather
    than grow a second, empty vault — as a `VaultLockUnavailableError`, since a vault
    that is not there is a vault whose lock cannot be taken.
    """
    lock_path = vault / LOCK_NAME
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise _unavailable(lock_path, exc) from exc
    try:
        try:
            _wait_for_lock(fd, lock_path, wait_s=DEFAULT_WAIT_S if wait_s is None else wait_s)
            _write_record(fd)
        except OSError as exc:
            # `VaultLockedError` is deliberately not an `OSError`, so a refusal passes
            # through untouched here and only a broken mechanism is renamed.
            raise _unavailable(lock_path, exc) from exc
        yield lock_path
    finally:
        # The release: closing the last descriptor on the open file description drops
        # its `flock`. No explicit `LOCK_UN` — it would be the same syscall's effect a
        # line earlier, and one whose failure there would be nothing to act on.
        os.close(fd)
