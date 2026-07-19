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


def test_list_missing_folder_is_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "Sources").mkdir()  # a vault without a Queue/ folder
    assert main(["--vault", str(tmp_path), "list"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["files"] == []
    assert out["counts"] == {"total": 0, "malformed": 0}


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
