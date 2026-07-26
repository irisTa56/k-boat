"""End-to-end tests for the `kboat-repos` CLI: subcommand dispatch and `write`."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from kboat.frontmatter import parse_frontmatter
from kboat.lock import vault_lock
from kboat.repos.__main__ import main
from kboat.repos.write import main as write_main

RECORD: dict[str, Any] = {
    "slug": "abc123def456",
    "url": "https://github.com/google/A2A",
    "title": "google/A2A",
    "fields": {"description": "An open protocol: agents talk.", "status": "recent"},
    "role": "framework",
    "domain": ["ai-agents"],
    "summary": "エージェント間通信のプロトコル。",
}


def _stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def test_usage_and_unknown_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert main(["--help"]) == 0
    assert main(["bogus"]) == 2
    assert "unknown subcommand: 'bogus'" in capsys.readouterr().err


def test_gather_dispatches(capsys: pytest.CaptureFixture[str]) -> None:
    # A non-repo URL is decided before `gh` is reached, so this exercises the
    # dispatch without a network call.
    assert main(["gather", "https://example.com/not-github"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "skip-not-a-repo"


def test_refresh_dispatches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["refresh", "--vault", str(tmp_path)]) == 1  # no Repos/ under the vault
    assert "no Repos/ directory" in json.loads(capsys.readouterr().out)["error"]


def test_write_dispatches_and_creates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stdin(monkeypatch, json.dumps(RECORD))
    assert main(["write", "--vault", str(tmp_path), "--today", "2026-06-06"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "created", "slug": "abc123def456", "path": "Repos/abc123def456.md"}
    assert (tmp_path / "Repos" / "abc123def456.md").exists()


def test_write_reports_dropped_keys_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The skill reads `dropped_fields` off the CLI's JSON, so that is where the
    # report has to survive — not just in the library's return value.
    _stdin(
        monkeypatch, json.dumps({**RECORD, "fields": {**RECORD["fields"], "descrption": "typo"}})
    )
    assert write_main(["--vault", str(tmp_path), "--today", "2026-06-06"]) == 0
    assert json.loads(capsys.readouterr().out)["dropped_fields"] == ["descrption"]


def test_write_collision_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stdin(monkeypatch, json.dumps(RECORD))
    write_main(["--vault", str(tmp_path), "--today", "2026-06-06"])
    capsys.readouterr()

    _stdin(monkeypatch, json.dumps({**RECORD, "url": "https://github.com/evil/clone"}))
    assert write_main(["--vault", str(tmp_path), "--today", "2026-06-06"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "collision"


@pytest.mark.parametrize(
    ("stdin", "diagnostic"),
    [
        ("not json", "not valid JSON"),
        ('["slug"]', "must be a JSON object"),
        ('{"slug": "s"}', "missing required keys: url, title"),
        (json.dumps({**RECORD, "fields": "not an object"}), "'fields' must be a JSON object"),
    ],
)
def test_write_rejects_a_record_it_cannot_use(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    stdin: str,
    diagnostic: str,
) -> None:
    # Exit 2 (bad input), not a traceback — and the stderr line is the product:
    # the record is assembled by an agent, which reads it to know what to re-send.
    _stdin(monkeypatch, stdin)
    assert write_main(["--vault", str(tmp_path)]) == 2
    assert diagnostic in capsys.readouterr().err
    assert not (tmp_path / "Repos").exists()


def test_write_reports_an_unreadable_existing_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The slug is taken by a file with no frontmatter at all. That is a note to
    # repair, so it exits 1 with a diagnostic rather than a traceback.
    path = tmp_path / "Repos" / "abc123def456.md"
    path.parent.mkdir(parents=True)
    path.write_text("no frontmatter here\n")

    _stdin(monkeypatch, json.dumps(RECORD))
    assert write_main(["--vault", str(tmp_path), "--today", "2026-06-06"]) == 1
    assert "write failed:" in capsys.readouterr().err
    assert path.read_text() == "no frontmatter here\n"


def test_write_requires_a_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        write_main([])
    assert excinfo.value.code == 2


def test_write_rejects_a_malformed_today(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        write_main(["--vault", str(tmp_path), "--today", "6 June"])
    assert excinfo.value.code == 2


def test_write_stamps_today_in_canonical_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `fromisoformat` also reads the basic form, and the stamp goes into the
    # note as given — so what a date parser accepts is not what a note may hold.
    _stdin(monkeypatch, json.dumps(RECORD))
    assert write_main(["--vault", str(tmp_path), "--today", "20260606"]) == 0
    fm = parse_frontmatter((tmp_path / "Repos" / "abc123def456.md").read_text())
    assert fm["added_date"] == "2026-06-06"


def test_write_refuses_a_locked_vault(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    brief_lock_wait: None,
) -> None:
    _stdin(monkeypatch, json.dumps(RECORD))
    with vault_lock(tmp_path):
        rc = write_main(["--vault", str(tmp_path), "--today", "2026-06-06"])
    assert rc == 1
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["status"] == "locked"
    assert out["holder"]["pid"] == os.getpid()
    assert "vault is locked" in captured.err
    assert not (tmp_path / "Repos" / "abc123def456.md").exists()


def test_refresh_dry_run_reads_a_locked_vault(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Read-only, so it neither takes the lock nor waits on one.
    (tmp_path / "Repos").mkdir()
    with vault_lock(tmp_path):
        rc = main(["refresh", "--vault", str(tmp_path), "--dry-run", "--today", "2026-06-06"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["counts"]["total"] == 0
