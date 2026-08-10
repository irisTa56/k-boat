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
import errno
import os
import stat
import tempfile
from collections.abc import Container, Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

# iCloud replaces an evicted file with a placeholder of this shape beside where
# the file was: `Sources/abc.md` becomes `Sources/.abc.md.icloud`. The vault is
# iCloud-synced, so any tool that decides something from a file's absence has to
# be able to tell "not there" from "not here yet".
ICLOUD_GLOB = ".*.icloud"

# A directory that lists but cannot be opened through — the message this module
# raises with, and the one `kboat.doctor` tags its own probe with. The two uses
# are independent: nothing branches on the text raised from here, and `doctor`
# does not call `list_note_dir`. It is one declaration so the wording a reader
# meets is the same wherever the condition is reported, not because a severity
# hangs off this string reaching across the two.
NOT_TRAVERSABLE = "Permission denied (not traversable)"


def icloud_placeholder(path: Path) -> Path:
    """Where iCloud leaves its marker when `path` is evicted."""
    return path.parent / f".{path.name}.icloud"


def name_taken(path: Path) -> bool:
    """Whether a name is spoken for — by a file, by one iCloud has evicted, or by a symlink.

    The question to ask before claiming a name, and the one a bare `Path.exists()`
    answers wrongly twice over. A placeholder means the file is not gone but not
    here yet, so writing at that name claims an identity another file still holds;
    iCloud settles the two later by suffixing or dropping one, which is a duplicate
    arriving quietly, long after the run that made it. And presence is asked without
    following symlinks, for the reason `kboat.doctor` gives where it asks the same
    way: a dangling symlink is a name already taken, and a writer told it is free
    replaces the link rather than the file it points at.

    **Raises** rather than answering when the vault refuses the read. `lstat`, not
    `Path.exists`, is what makes that possible: from CPython 3.14 `exists()` is
    `os.path.exists` (and `os.path.lexists` under `follow_symlinks=False`), and both
    swallow every `OSError` and return `False` — so on the interpreter this
    workspace runs, a permission-denied probe would come back as a free name, which
    is the one wrong answer this exists to prevent. Only the
    two errors that genuinely mean "no such name" are read as free; the caller
    decides what the rest are, and each has a per-item boundary that says so.
    """
    return name_occupied(path) or name_occupied(icloud_placeholder(path))


def name_occupied(path: Path) -> bool:
    """Whether anything is at `path` itself — **raising** where the vault refuses.

    `name_taken`'s first half, split out because the classifiers need it. They ask
    three questions, not two: is a *file* there, is anything else there, is there
    a placeholder. Without the middle one a directory or a dangling symlink at a
    slug falls through to the placeholder test, and a stale stub beside it then
    answers for it — reporting a name no download will ever free as one that is
    only waiting on iCloud, which both skills route to nobody.

    `lstat`, so a dangling symlink counts: the name is spoken for either way.
    """
    try:
        path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def file_present(path: Path) -> bool:
    """Whether a file is at `path` — **raising** where the vault refuses the read.

    `name_taken`'s companion, for the caller that has been told a name is spoken
    for and has to say by what. `Path.exists()` cannot do that job: it swallows the
    refusal, so a symlink into an unreadable tree comes back as "no file here" and
    the caller reports a name held by something no run will free, sending a human
    to hunt a broken symlink that is not there. Following symlinks is deliberate —
    the question is whether a *file* is there, and a dangling link is the case that
    must answer no.
    """
    try:
        mode = path.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return False
    # A *file*, as the name says: a directory at a note's slug is a name held by
    # something that is not a note, and the two answers route to opposite remedies
    # — merge the two notes, versus a name no run will free.
    return stat.S_ISREG(mode)


@dataclass(frozen=True)
class StubProbe:
    """What a writer about to vacate a name learned about the stub beside it.

    Three answers, not two, and **no raise** — which is the opposite of the other
    probes here and deliberate. They refuse because guessing wrong makes a writer
    claim a name it should not have; this one is asked *after* that decision, about
    a name the writer is giving up. Refusing to answer would abort a rename over a
    stub, and the probe itself can fail on the very names this repair exists for: a
    filename of 248 bytes or more makes its `.icloud` sibling exceed the 255-byte
    limit, so the answer is unobtainable exactly where the note most needs moving —
    and the rename is what makes the name probeable again.

    So "I could not tell" is a report, never a refusal.
    """

    stub: Path | None = None
    unknown: str | None = None


def stranded_stub(path: Path) -> StubProbe:
    """The placeholder beside `path`, for a writer about to vacate that name.

    The other half of the rule `evictions` states. A stub beside its own present
    file is not an eviction *while the file is there* — but a writer that renames
    or unlinks the file makes it one, and from then on the stub is a lone
    placeholder that fails `kboat-doctor`'s `icloud_notes` and stops the routine
    every day, out of a report that never mentioned it. So the two halves belong
    together: the reporting side skips the pair, and the side that breaks the pair
    says so. Removing the stub is nobody's here — deleting a placeholder is how a
    file leaves iCloud.
    """
    stub = icloud_placeholder(path)
    try:
        return StubProbe(stub=stub if file_present(stub) else None)
    except OSError as exc:
        return StubProbe(unknown=str(exc))


def evictions(names: Iterable[str], present: Container[str]) -> list[str]:
    """The placeholders among `names` that are evictions, given what is `present`.

    A placeholder sitting beside its own present file is a stale stub, not an
    eviction: the file is here, so reporting it would tell a reader the wait is on
    a download that already happened, in the same report that shows the file was
    read. It is the file-before-placeholder precedence the writers apply when they
    claim a name, asked of the reporting side.

    One function rather than the predicate written out at each report site, because
    two copies of it are two specifications: they drifted on their first day over
    what "present" means, and the sites they feed are a precondition and the passes
    it gates — which must not disagree about whether a placeholder is an eviction.
    """
    return [n for n in names if fnmatch(n, ICLOUD_GLOB) and n[1 : -len(".icloud")] not in present]


def list_note_dir(directory: Path) -> tuple[list[Path], list[Path]]:
    """One note directory's `*.md` files and its iCloud placeholders, both sorted.

    One listing rather than two globs, and **raising** rather than coming back
    empty, for the reason `name_taken` raises: `pathlib.glob` swallows the
    `PermissionError` that `os.scandir` raises, so a directory the OS refuses to
    list reads as an empty, clean one — and `is_dir()` still answers `True`, since
    that `stat` goes through the parent. A scan that took the empty answer would
    report a vault it never read as a vault with nothing in it, which is the same
    silence an eviction produces and the same one this module exists to break.

    A directory that is simply not there is not that: it comes back as two empty
    lists, since creating it belongs to declaring a note type and its absence is
    `kboat-doctor`'s to report.
    """
    try:
        entries = sorted(directory.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return [], []
    # Listable is not usable, and `iterdir` only answers the first. An `r--`
    # directory lists its names and refuses every `read_text` beneath it, so a
    # caller would get one note-shaped failure per note for one vault-shaped
    # cause — and, where a caller counts unread directories, a count of nought.
    # `kboat.doctor` probes this same way; the two must not disagree about
    # whether a directory was read.
    if not os.access(directory, os.X_OK):
        raise PermissionError(errno.EACCES, NOT_TRAVERSABLE, str(directory))
    # `*.md` exactly as `glob` matched it, dot-prefixed names included, and matched
    # on the name rather than on `suffix` because `Path(".md").suffix` is `""`. A
    # name this split drops belongs to neither list and is reported by nothing,
    # which is the silence the whole helper is against.
    notes = [p for p in entries if p.name.endswith(".md")]
    # Only the placeholders shadowing a `*.md` name: this helper answers about
    # notes, and its callers say so in words ("the note could not be read"). An
    # evicted attachment beside a note is a real eviction and not one of those,
    # so it belongs to a sweep that reports files — `kboat-doctor`'s, which
    # calls `evictions` directly and keeps its breadth.
    by_name = {p.name: p for p in entries}
    shadowed = evictions(by_name, by_name)
    return notes, [by_name[n] for n in shadowed if n[1 : -len(".icloud")].endswith(".md")]


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
