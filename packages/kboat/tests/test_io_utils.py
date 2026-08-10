"""Behavior tests for the shared atomic writer.

The durability guarantees are not observable from the filesystem after the fact
— a power loss is what would tell them apart — so the fsync smoke test asserts
the syscalls instead: the content is flushed before the rename and the directory
entry after it. Everything else is checked against real files.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kboat.io_utils import (
    atomic_write_text,
    file_present,
    icloud_placeholder,
    list_note_dir,
    name_taken,
)


def test_a_name_an_icloud_placeholder_holds_is_taken(tmp_path: Path) -> None:
    # The distinction `Path.exists()` cannot draw: an evicted file answers `False`
    # exactly as a name nothing occupies does, and the two are opposite answers to
    # "may I write here?".
    free = tmp_path / "note.md"
    assert not name_taken(free)

    evicted = tmp_path / "evicted.md"
    icloud_placeholder(evicted).write_bytes(b"")
    assert not evicted.exists()
    assert name_taken(evicted)

    local = tmp_path / "local.md"
    local.write_text("x\n", encoding="utf-8")
    assert name_taken(local)


def test_a_name_a_dangling_symlink_holds_is_taken(tmp_path: Path) -> None:
    # `exists()` follows the link and calls it missing, which would have a writer
    # replace the link instead of the file it names. `kboat.doctor` answers the
    # same question the same way.
    dangling = tmp_path / "dangling.md"
    dangling.symlink_to(tmp_path / "nowhere.md")
    assert not dangling.exists()
    assert name_taken(dangling)


def test_a_refused_read_raises_rather_than_answering_free(tmp_path: Path) -> None:
    # The answer that must never be given by default: `Path.exists` swallows every
    # `OSError` from CPython 3.14 on, so a probe the vault refuses would come back
    # as a free name and the writer would claim it.
    directory = tmp_path / "Repos"
    directory.mkdir()
    (directory / ".abc123.md.icloud").write_bytes(b"")
    directory.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            name_taken(directory / "abc123.md")
    finally:
        directory.chmod(0o755)


def test_list_note_dir_splits_notes_from_placeholders(tmp_path: Path) -> None:
    directory = tmp_path / "Sources"
    directory.mkdir()
    (directory / "b.md").write_text("x\n", encoding="utf-8")
    (directory / "a.md").write_text("x\n", encoding="utf-8")
    (directory / ".evicted.md.icloud").write_bytes(b"")
    (directory / "notes.txt").write_text("x\n", encoding="utf-8")
    # `glob("*.md")` matches all three of these, so this must too: one it dropped
    # would be in neither list and reported by nothing. `.md` is the exemplar that
    # matters — its `Path.suffix` is `""`, so a suffix test passes the other two
    # and silently loses this one.
    (directory / ".hidden.md").write_text("x\n", encoding="utf-8")
    (directory / ".md").write_text("x\n", encoding="utf-8")

    # A stub beside its own present file is a stale leftover, not an eviction:
    # reporting it would tell a caller the wait is on a download that already
    # happened, in the same report that shows the note was read.
    (directory / ".b.md.icloud").write_bytes(b"")

    # An evicted attachment beside a note is a real eviction and not a note one:
    # this helper answers about notes, and its callers say "the note could not be
    # read" in words. `kboat-doctor` reports files and keeps the breadth.
    (directory / ".diagram.png.icloud").write_bytes(b"")

    notes, placeholders = list_note_dir(directory)

    assert [p.name for p in notes] == [".hidden.md", ".md", "a.md", "b.md"]
    assert [p.name for p in notes] == sorted(p.name for p in directory.glob("*.md"))
    assert [p.name for p in placeholders] == [".evicted.md.icloud"]


def test_list_note_dir_reads_an_absent_directory_as_empty(tmp_path: Path) -> None:
    # Creating it belongs to declaring a note type, and its absence is
    # `kboat-doctor`'s to report — so it is not this helper's refusal.
    assert list_note_dir(tmp_path / "Nope") == ([], [])


def test_list_note_dir_raises_on_a_directory_it_cannot_list(tmp_path: Path) -> None:
    # `Path.glob` would swallow this and hand back an empty directory, so a scan
    # would report a vault it never read as a vault with nothing in it — the same
    # silence an eviction produces, from the other direction.
    directory = tmp_path / "Sources"
    directory.mkdir()
    (directory / "a.md").write_text("x\n", encoding="utf-8")
    directory.chmod(0o111)
    try:
        assert list(directory.glob("*.md")) == [], "the answer this helper must not give"
        with pytest.raises(PermissionError):
            list_note_dir(directory)
    finally:
        directory.chmod(0o755)


def test_list_note_dir_raises_on_a_directory_it_cannot_traverse(tmp_path: Path) -> None:
    # `r--` lists its names and refuses every read beneath it, so `iterdir` alone
    # answers "fine" and the caller gets one note-shaped failure per note for a
    # single vault-shaped cause — and a count of unread directories of nought.
    directory = tmp_path / "Sources"
    directory.mkdir()
    (directory / "a.md").write_text("x\n", encoding="utf-8")
    directory.chmod(0o444)
    try:
        assert [p.name for p in directory.iterdir()] == ["a.md"], "listing alone says fine"
        with pytest.raises(PermissionError, match="not traversable"):
            list_note_dir(directory)
    finally:
        directory.chmod(0o755)


def test_file_present_raises_where_exists_would_call_a_refused_read_absent(
    tmp_path: Path,
) -> None:
    # The caller has been told the name is taken and has to say by what. `exists()`
    # answers "no file" for a link into an unreadable tree, so the caller reports a
    # name nothing will free and a human hunts a broken symlink that is not there.
    walled = tmp_path / "walled"
    walled.mkdir()
    (walled / "real.md").write_text("x\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(walled / "real.md")
    walled.chmod(0o000)
    try:
        assert name_taken(link), "the name is spoken for"
        assert not link.exists(), "the answer this helper must not give"
        with pytest.raises(PermissionError):
            file_present(link)
    finally:
        walled.chmod(0o755)

    dangling = tmp_path / "dangling.md"
    dangling.symlink_to(tmp_path / "nowhere.md")
    assert not file_present(dangling), "a dangling link is the case that answers no"

    # A *file*, as the name says. A directory at a note's slug routes to the
    # opposite remedy from a note there: a name no run frees, not a merge.
    directory = tmp_path / "dir.md"
    directory.mkdir()
    assert not file_present(directory)


def test_writes_content_and_creates_missing_parents(tmp_path: Path) -> None:
    target = tmp_path / "Sources" / "note.md"
    atomic_write_text(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_overwrites_in_place_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    atomic_write_text(target, "first\n")
    atomic_write_text(target, "second\n")
    assert target.read_text(encoding="utf-8") == "second\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["note.md"]


def test_fsyncs_the_content_before_the_rename_and_the_directory_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def spy_fsync(fd: int) -> None:
        # A directory fd is what the post-rename fsync holds; the content fsync
        # holds the temp file's. Telling them apart is the whole assertion.
        events.append("fsync_dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync_file")
        real_fsync(fd)

    def spy_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)
    atomic_write_text(tmp_path / "note.md", "durable\n")

    assert events == ["fsync_file", "replace", "fsync_dir"]


def test_a_failed_rename_leaves_neither_target_nor_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    target = tmp_path / "note.md"
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "never lands\n")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_failed_directory_fsync_does_not_fail_a_write_that_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The contract every caller relies on: a raise means nothing was written. The
    # directory flush runs after the rename, so raising from it would have them
    # all report a note that is on disk as lost — and, on the repo-refresh rename
    # path, abandon the old note beside the new one.
    real_fsync = os.fsync

    def fail_on_dir(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("no fsync here")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_on_dir)
    target = tmp_path / "note.md"
    atomic_write_text(target, "landed\n")

    assert target.read_text(encoding="utf-8") == "landed\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["note.md"]


def test_a_directory_that_cannot_be_opened_does_not_fail_a_write_that_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same contract, at the other syscall the flush makes: the fd it needs is
    # taken after the rename too, so failing to get one cannot cost the write.
    real_open = os.open

    def fail_on_dir(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if flags == os.O_RDONLY:
            raise OSError("no directory fd for you")
        return real_open(path, flags, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(os, "open", fail_on_dir)
    target = tmp_path / "note.md"
    atomic_write_text(target, "landed\n")

    assert target.read_text(encoding="utf-8") == "landed\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["note.md"]


@pytest.mark.parametrize("mode", [0o664, 0o600, 0o640])
def test_a_rewrite_keeps_the_file_s_own_permissions(tmp_path: Path, mode: int) -> None:
    # A replace must not be a way to change a file's mode: `mkstemp` creates at
    # 0o600 and `os.replace` carries that onto the target, so without care an
    # in-place rewrite would narrow a note a human had made readable. Several modes,
    # so the assertion cannot be satisfied by landing on one fixed value.
    target = tmp_path / "note.md"
    target.write_text("first\n", encoding="utf-8")
    target.chmod(mode)

    atomic_write_text(target, "second\n")

    assert stat.S_IMODE(target.stat().st_mode) == mode


def test_a_file_created_here_keeps_the_temp_file_s_private_mode(tmp_path: Path) -> None:
    # Nothing is widened on create. Picking a mode here would mean ignoring the
    # caller's umask, and widening a fresh file is not this writer's decision.
    target = tmp_path / "new.md"
    atomic_write_text(target, "fresh\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_a_mode_that_cannot_be_applied_fails_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `fchmod` runs before the rename, so a filesystem that refuses it fails a write
    # that has not happened — the "a raise means nothing was written" contract. Move
    # it after the rename and the target would be left half-updated instead.
    target = tmp_path / "note.md"
    target.write_text("first\n", encoding="utf-8")
    target.chmod(0o664)

    def boom(*_args: object) -> None:
        raise OSError("no chmod here")

    monkeypatch.setattr(os, "fchmod", boom)
    with pytest.raises(OSError, match="no chmod here"):
        atomic_write_text(target, "second\n")

    assert target.read_text(encoding="utf-8") == "first\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["note.md"]


def test_a_target_that_cannot_be_stat_ed_is_raised_not_defaulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only absence means "there is no mode to preserve". Reading a denied or broken
    # `stat` as absence would land the rewrite at the temp file's mode, which is the
    # silent permission change the preservation exists to prevent.
    target = tmp_path / "note.md"
    target.write_text("first\n", encoding="utf-8")
    real_stat = Path.stat

    def denied(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == target:
            raise PermissionError("not yours")
        return real_stat(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(PermissionError, match="not yours"):
        atomic_write_text(target, "second\n")

    assert target.read_text(encoding="utf-8") == "first\n"
