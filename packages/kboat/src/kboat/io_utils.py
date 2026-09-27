"""Shared I/O helpers.

`atomic_write_text` is the single writer for every durable file this workspace's
tools write. A file an agent authors with its own editor is outside it, as
`kboat-vault-conventions` records. Four guarantees:

- A crash mid-write never leaves a truncated note at the target path.
- iCloud (the vault is iCloud-synced) sees the new content appear in one step,
  so it never picks up and syncs a half-written note.
- The content is durable before the rename and the rename is durable after it,
  so a power loss cannot leave the directory entry pointing at content that
  never reached the disk — the failure an atomic rename alone does not cover.
- An existing file's **permissions** come back as they were (see `_preserved_mode`).
  Only those: a rename replaces the inode, so anything else attached to the old one
  — extended attributes, ACLs, a per-file `com.apple.macl` grant, the creation time —
  does not survive a rewrite. `kboat-vault-conventions` records that consequence
  where it can reach a reader who would otherwise be surprised by it.

The temp file lives in the same directory so the rename stays on one filesystem
(``os.replace`` across filesystems falls back to copy + unlink and loses
atomicity).

**A raise means nothing was written.** Every caller maps an `OSError` from here
to a write that did not happen — reporting the failure, or leaving the entry for
the next run to retry — so a failure after the rename would have them all report
a note that is on disk as lost. The post-rename directory flush is therefore
best-effort: it cannot un-write the rename it is flushing, and the content is
already durable from its own `fsync`, so the barrier's failure costs the weakest
of the four guarantees rather than the caller's contract. Every other step runs
before the rename, so raising from one is the contract rather than a breach of it.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import tempfile
from pathlib import Path

# A directory that lists but cannot be opened through — the message this module
# raises with, the one `kboat.doctor` tags its own probe with, and the one
# `kboat.note.migrate` reports a `PDFs/` it cannot probe through with. The uses
# are independent: nothing branches on the text. It is one declaration so the
# wording a reader meets is the same wherever the condition is reported, not
# because a severity hangs off this string reaching across them.
NOT_TRAVERSABLE = "Permission denied (not traversable)"


def name_occupied(path: Path) -> bool:
    """Whether a name is spoken for — by a file or by anything else, a symlink included.

    The question to ask before claiming a name, and one a bare `Path.exists()`
    answers wrongly twice over. Presence is asked without following symlinks, for
    the reason `kboat.doctor` gives where it asks the same way: a dangling symlink
    is a name already taken, and a writer told it is free replaces the link rather
    than the file it points at.

    **Raises** rather than answering when the vault refuses the read. `lstat`, not
    `Path.exists`, is what makes that possible: from CPython 3.14 `exists()` is
    `os.path.exists` (and `os.path.lexists` under `follow_symlinks=False`), and both
    swallow every `OSError` and return `False` — so on the interpreter this
    workspace runs, a permission-denied probe would come back as a free name, which
    is the one wrong answer this exists to prevent. Only the two errors that
    genuinely mean "no such name" are read as free; the caller decides what the
    rest are, and each has a per-item boundary that says so.
    """
    try:
        path.lstat()
    except FileNotFoundError, NotADirectoryError:
        return False
    return True


def file_present(path: Path) -> bool:
    """Whether a file is at `path` — **raising** where the vault refuses the read.

    `name_occupied`'s companion, for the caller that has been told a name is spoken
    for and has to say by what. `Path.exists()` cannot do that job: it swallows the
    refusal, so a symlink into an unreadable tree comes back as "no file here" and
    the caller reports a name held by something no run will free, sending a human
    to hunt a broken symlink that is not there. Following symlinks is deliberate —
    the question is whether a *file* is there, and a dangling link is the case that
    must answer no.
    """
    try:
        mode = path.stat().st_mode
    except FileNotFoundError, NotADirectoryError:
        return False
    # A *file*, as the name says: a directory at a note's slug is a name held by
    # something that is not a note, and the two answers route to opposite remedies
    # — merge the two notes, versus a name no run will free.
    return stat.S_ISREG(mode)


def list_note_dir(directory: Path, *, required: bool = False) -> list[Path]:
    """One note directory's `*.md` files, sorted.

    **Raising** rather than coming back empty, for the reason `name_occupied`
    raises: `pathlib.glob` swallows the `PermissionError` that `os.scandir` raises,
    so a directory the OS refuses to list reads as an empty, clean one — and
    `is_dir()` still answers `True`, since that `stat` goes through the parent. A
    scan that took the empty answer would report a vault it never read as a vault
    with nothing in it.

    A directory that is simply not there, or a name held by something that is not
    one, comes back as an empty list unless it is `required`, in which case the
    `FileNotFoundError` or `NotADirectoryError` is raised like any refusal. A
    command reading a folder in the vault's required set passes `required`, since
    for it an absent folder is a vault that has not synced rather than one with
    nothing in it (`kboat-vault-conventions` "Vault preconditions"); a report-only
    scan such as `kboat-validate`'s, or one over an optional folder, does not.
    """
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError, NotADirectoryError:
        if required:
            raise
        return []
    # Listable is not usable, and `iterdir` only answers the first. An `r--`
    # directory lists its names and refuses every `read_text` beneath it, so a
    # caller would get one note-shaped failure per note for one vault-shaped
    # cause — and, where a caller counts unread directories, a count of nought.
    # `kboat.doctor` probes this same way; the two must not disagree about
    # whether a directory was read.
    if not os.access(directory, os.X_OK):
        raise PermissionError(errno.EACCES, NOT_TRAVERSABLE, str(directory))
    # `*.md` exactly as `glob` matched it, dot-prefixed names included, and matched
    # on the name rather than on `suffix` because `Path(".md").suffix` is `""`.
    return [p for p in entries if p.name.endswith(".md")]


def unread_dir(exc: OSError) -> str:
    """Which of the three ways a required folder could not be read, in one wording.

    For the `error` a command reports under the folder's own name when
    `list_note_dir(..., required=True)` raises. The three read differently to the
    human who has to act — create or sync the folder, free a name something else
    holds, or restore a permission — so the report leads with which it was rather
    than leaving it to an `strerror` deep in the text. Every command shares this so
    a reader meets one wording for one state whichever report it is reading.

    A name held by a dangling symlink raises `FileNotFoundError` too, and is not
    absent: `mkdir` there fails, so it is reported as the name something that is not
    a directory holds, as `kboat-doctor`'s `folders_occupied` files it.
    """
    if isinstance(exc, FileNotFoundError):
        held = False
        if exc.filename is not None:
            # Only sharpens the wording, so a probe that cannot answer leaves "absent".
            with contextlib.suppress(OSError):
                held = name_occupied(Path(exc.filename))
        if held:
            return f"not a directory: {exc.filename} is held by something that is not one"
        return f"absent: {exc}"
    if isinstance(exc, NotADirectoryError):
        return f"not a directory: {exc}"
    return f"refused: {exc}"


def fsync_dir(directory: Path) -> None:
    """Flush the directory entry itself, so a rename survives a power loss.

    **Raises**, because its other caller uses it as a barrier rather than as a
    last step: `kboat.note.migrate` moves a note and its PDF as a pair, and the
    flush between the two renames is what stops a power loss from keeping the
    second and losing the first. A barrier that quietly did nothing would leave
    that pair split with nothing to report and no later scan looking for it.

    `atomic_write_text` wants the opposite and says so where it calls this: there
    the flush is the last step, cannot un-write the rename it is flushing, and so
    must not turn a landed write into a reported failure.
    """
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _preserved_mode(path: Path) -> int | None:
    """The mode a rewrite has to land with, or `None` when there is no file yet.

    `mkstemp` creates at `0o600` and `os.replace` carries the temp file's mode onto
    the target, so without this an in-place rewrite would narrow a file a human or
    another tool had made readable.

    A file this writer *creates* keeps `mkstemp`'s mode rather than one chosen here.
    Choosing one would mean either ignoring the caller's umask or reading it back
    through `os.umask`, which has no getter and so cannot be read without briefly
    setting it — and widening a fresh file is not this writer's decision to make.
    A `stat` that fails for any reason other than absence is raised, not defaulted:
    it happens before the rename, so it costs a write that has not happened.
    """
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = _preserved_mode(path)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            if mode is not None:
                # Before the flush, so the mode is part of what `fsync` makes
                # durable, and before the rename, so a filesystem that refuses it
                # fails a write that has not happened rather than one that has.
                os.fchmod(f.fileno(), mode)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        # Suppressed here and nowhere else: the rename has landed, so a
        # filesystem that will not flush a directory must not make this raise
        # and have every caller report a write that did happen as lost.
        with contextlib.suppress(OSError):
            fsync_dir(path.parent)
    except BaseException:
        # After a successful rename the temp path is already gone, so this only
        # cleans up a write that never became the target file.
        tmp.unlink(missing_ok=True)
        raise
