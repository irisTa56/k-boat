"""End-to-end tests for the `kboat-queue` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
