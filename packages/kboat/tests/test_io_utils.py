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

from kboat.io_utils import atomic_write_text


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
