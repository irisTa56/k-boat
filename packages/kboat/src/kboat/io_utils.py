"""Shared I/O helpers.

`atomic_write_text` is the single writer for every durable file this workspace's
tools write — the notes `kboat.write` assembles, the in-place frontmatter
rewrites the lifecycle, daily-pick and repo-refresh CLIs make, and feed-filter's
`sites.toml`. A file an agent authors with its own editor is outside it, as
`kboat-vault-conventions` records. Writes go to a sibling temp file in the same
directory and are renamed into place with ``os.replace`` — one atomic syscall on
POSIX. Four guarantees:

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
import os
import stat
import tempfile
from pathlib import Path

# iCloud replaces an evicted file with a placeholder of this shape beside where
# the file was: `Sources/abc.md` becomes `Sources/.abc.md.icloud`. The vault is
# iCloud-synced, so any tool that decides something from a file's absence has to
# be able to tell "not there" from "not here yet".
ICLOUD_GLOB = ".*.icloud"


def icloud_placeholder(path: Path) -> Path:
    """Where iCloud leaves its marker when `path` is evicted."""
    return path.parent / f".{path.name}.icloud"


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
