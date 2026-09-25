"""End-to-end tests for the `kboat-queue` CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kboat.lock import vault_lock
from kboat.queue.__main__ import main


def _queue(vault: Path) -> Path:
    q = vault / "Queue"
    q.mkdir(parents=True, exist_ok=True)
    return q


def test_list_parses_captures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    q = _queue(tmp_path)
    (q / "kboat-queue-1.md").write_text("[First](https://example.com/a)\n", encoding="utf-8")
    (q / "kboat-queue-2.md").write_text(
        "[Wiki](https://en.wikipedia.org/wiki/Foo_(bar))\n", encoding="utf-8"
    )
    assert main(["--vault", str(tmp_path), "list"]) == 0
    out = json.loads(capsys.readouterr().out)
    urls = {f["url"] for f in out["files"]}
    assert urls == {"https://example.com/a", "https://en.wikipedia.org/wiki/Foo_(bar)"}
    assert out["counts"] == {"total": 2, "malformed": 0}
    assert out["folder"] == "Queue"
    assert all(f["path"].startswith("Queue/") for f in out["files"])


def test_list_flags_malformed_capture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    q = _queue(tmp_path)
    (q / "kboat-queue-1.md").write_text("[ok](https://example.com)\n", encoding="utf-8")
    (q / "kboat-queue-2.md").write_text("no link here\n", encoding="utf-8")
    assert main(["--vault", str(tmp_path), "list"]) == 0
    out = json.loads(capsys.readouterr().out)
    bad = next(f for f in out["files"] if "error" in f)
    assert bad["url"] is None and bad["error"] == "no_url"
    assert out["counts"]["malformed"] == 1


def test_list_reports_unreadable_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A capture path that cannot be read (here a directory shaped like a note)
    # is reported with the OSError message, not skipped silently or crashed on.
    q = _queue(tmp_path)
    (q / "kboat-queue-1.md").write_text("[ok](https://example.com)\n", encoding="utf-8")
    (q / "broken.md").mkdir()
    assert main(["--vault", str(tmp_path), "list"]) == 0
    out = json.loads(capsys.readouterr().out)
    broken = next(f for f in out["files"] if f["path"].endswith("broken.md"))
    assert broken["url"] is None and broken["error"]
    assert out["counts"]["malformed"] == 1


def test_list_reports_a_capture_that_is_not_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    queue = tmp_path / "Queue"
    queue.mkdir()
    (queue / "bad.md").write_bytes(b"[t](https://example.com/\xff)\n")
    main(["--vault", str(tmp_path), "list"])
    out = json.loads(capsys.readouterr().out)
    broken = next(f for f in out["files"] if f["path"] == "Queue/bad.md")
    assert broken["url"] is None and broken["error"]


def _unread(vault: Path, capsys: pytest.CaptureFixture[str]) -> list[tuple[str, str]]:
    """Run a list that could not read its folder: exit 1, the report still printed."""
    assert main(["--vault", str(vault), "list"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["files"] == []
    assert out["counts"] == {"total": 0, "malformed": 0}
    return [(a["path"], a["error"].split(":")[0]) for a in out["anomalies"]]


def test_an_absent_queue_folder_is_not_a_drained_queue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `Queue/` is in the vault's required set: its absence is a vault that did not
    # sync, and an empty `files` alone is what tells ingest there is nothing to do.
    (tmp_path / "Sources").mkdir()
    assert _unread(tmp_path, capsys) == [("Queue", "absent")]


def test_a_queue_name_held_by_a_file_is_not_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "Queue").write_text("not a folder\n", encoding="utf-8")
    assert _unread(tmp_path, capsys) == [("Queue", "not a directory")]


def test_a_queue_name_held_by_a_dangling_symlink_is_not_called_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Listing through the link raises the same error an absent folder does, and
    # "absent" sends the human to a `mkdir` the name makes fail.
    (tmp_path / "Queue").symlink_to(tmp_path / "gone")
    assert _unread(tmp_path, capsys) == [("Queue", "not a directory")]


def test_a_vault_that_is_a_regular_file_is_not_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The parent is the file: the route a `Queue` that is itself a file misses.
    vault = tmp_path / "vault"
    vault.write_text("mis-typed --vault\n", encoding="utf-8")
    assert _unread(vault, capsys) == [("Queue", "not a directory")]


def test_a_queue_folder_the_os_will_not_list_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    q = _queue(tmp_path)
    (q / "kboat-queue-1.md").write_text("[ok](https://example.com)\n", encoding="utf-8")
    q.chmod(0o000)
    try:
        assert _unread(tmp_path, capsys) == [("Queue", "refused")]
    finally:
        q.chmod(0o755)


def test_an_evicted_capture_is_an_anomaly_not_a_drained_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    q = _queue(tmp_path)
    (q / "kboat-queue-1.md").write_text("[ok](https://example.com)\n", encoding="utf-8")
    (q / ".kboat-queue-2.md.icloud").write_bytes(b"")
    assert main(["--vault", str(tmp_path), "list"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [f["path"] for f in out["files"]] == ["Queue/kboat-queue-1.md"]
    assert [a["path"] for a in out["anomalies"]] == ["Queue/.kboat-queue-2.md.icloud"]


def test_list_custom_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    (inbox / "c.md").write_text("[t](https://example.com)\n", encoding="utf-8")
    assert main(["--vault", str(tmp_path), "--folder", "Inbox", "list"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["folder"] == "Inbox"
    assert out["files"][0]["url"] == "https://example.com"


def test_errors_without_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    with pytest.raises(SystemExit) as exc:  # argparse parser.error exits with code 2
        main(["list"])
    assert exc.value.code == 2


def _remove(vault: Path, path: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main(["--vault", str(vault), "remove", path])
    return code, capsys.readouterr().out


def test_remove_deletes_the_capture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    q = _queue(tmp_path)
    (q / "kboat-queue-1.md").write_text("[ok](https://example.com)\n", encoding="utf-8")
    code, out = _remove(tmp_path, "Queue/kboat-queue-1.md", capsys)
    assert code == 0
    assert json.loads(out) == {
        "path": "Queue/kboat-queue-1.md",
        "status": "removed",
        "stranded": None,
    }
    assert not (q / "kboat-queue-1.md").exists()


def test_remove_names_the_stub_it_strands_and_leaves_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Once the capture is gone the stub beside it is a lone placeholder, which
    # fails the next `kboat-doctor`; deleting it is how a file leaves iCloud.
    q = _queue(tmp_path)
    (q / "kboat-queue-1.md").write_text("[ok](https://example.com)\n", encoding="utf-8")
    (q / ".kboat-queue-1.md.icloud").write_bytes(b"")
    code, out = _remove(tmp_path, "Queue/kboat-queue-1.md", capsys)
    assert code == 0
    report = json.loads(out)
    assert report["status"] == "removed"
    assert report["stranded"] == "Queue/.kboat-queue-1.md.icloud"
    assert (q / ".kboat-queue-1.md.icloud").exists()


def test_remove_of_a_capture_already_gone_is_absent_not_a_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A rerun after a crash between the note write and the removal lands here.
    _queue(tmp_path)
    code, out = _remove(tmp_path, "Queue/kboat-queue-1.md", capsys)
    assert code == 0
    assert json.loads(out)["status"] == "absent"


def test_remove_of_an_evicted_capture_strands_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The stub is the capture itself, waiting on iCloud; `list` reports it as an
    # anomaly, and a removal that deleted nothing did not strand it.
    q = _queue(tmp_path)
    (q / ".kboat-queue-1.md.icloud").write_bytes(b"")
    code, out = _remove(tmp_path, "Queue/kboat-queue-1.md", capsys)
    assert code == 0
    assert json.loads(out) == {
        "path": "Queue/kboat-queue-1.md",
        "status": "absent",
        "stranded": None,
    }


@pytest.mark.parametrize(
    "path",
    [
        "Sources/note.md",
        "Queue/../Sources/note.md",
        "Queue/sub/note.md",
        "Queue/note.txt",
        "Queue/.note.md.icloud",
        "Queue",
    ],
)
def test_remove_refuses_anything_but_a_capture(
    tmp_path: Path, path: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # The allow rule that runs this unattended names the command, not the path,
    # so the command itself is what keeps it inside the queue.
    (tmp_path / "Sources").mkdir()
    (tmp_path / "Sources" / "note.md").write_text("keep\n", encoding="utf-8")
    q = _queue(tmp_path)
    (q / "sub").mkdir()
    for name in ("sub/note.md", "note.txt", ".note.md.icloud"):
        (q / name).write_text("keep\n", encoding="utf-8")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    with pytest.raises(SystemExit) as exc:
        main(["--vault", str(tmp_path), "remove", path])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


def test_remove_refuses_an_absolute_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    q = _queue(tmp_path)
    capture = q / "kboat-queue-1.md"
    capture.write_text("[ok](https://example.com)\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--vault", str(tmp_path), "remove", str(capture)])
    assert exc.value.code == 2
    assert capture.exists()


def test_remove_refuses_a_locked_vault_without_deleting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], brief_lock_wait: None
) -> None:
    q = _queue(tmp_path)
    capture = q / "kboat-queue-1.md"
    capture.write_text("[ok](https://example.com)\n", encoding="utf-8")
    with vault_lock(tmp_path):
        code, out = _remove(tmp_path, "Queue/kboat-queue-1.md", capsys)
    assert code == 1
    report = json.loads(out)
    assert report["status"] == "locked"
    assert report["holder"]["pid"] == os.getpid()
    assert capture.exists()


def test_remove_that_cannot_delete_reports_the_failure_with_no_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A directory shaped like a capture: the unlink raises, and a record on stdout
    # would read as a removal ingest could go on from.
    q = _queue(tmp_path)
    (q / "broken.md").mkdir()
    assert main(["--vault", str(tmp_path), "remove", "Queue/broken.md"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("remove failed: ")
    assert (q / "broken.md").is_dir()
