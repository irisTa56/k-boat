"""End-to-end tests for the `kboat-doctor` CLI."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from kboat.doctor.__main__ import main
from kboat.doctor.core import MAX_REPORT_PATHS

Evict = Callable[[Path, str], Path]


def test_healthy_vault_exits_zero(healthy_vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--vault", str(healthy_vault)]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] is True
    assert out["vault"] == str(healthy_vault)
    assert out["counts"]["ok"] == out["counts"]["total"]
    # Every status key is present even at zero, so a reader never has to treat an
    # absent key as "none".
    assert out["counts"]["failed"] == 0
    assert out["counts"]["warning"] == 0
    assert captured.err == ""


def test_failure_exits_one_and_lists_every_failed_check(
    healthy_vault: Path, evict: Evict, capsys: pytest.CaptureFixture[str]
) -> None:
    (healthy_vault / "Questions.md").unlink()
    (healthy_vault / "Queue").rmdir()
    evict(healthy_vault / "Sources", "abc.md")

    assert main(["--vault", str(healthy_vault)]) == 1
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] is False
    failed = {c["name"] for c in out["checks"] if c["status"] == "failed"}
    assert failed == {"required_folders", "questions_file", "icloud_notes"}
    assert out["counts"]["failed"] == 3
    assert out["counts"]["total"] == 9
    # Diagnostics on stderr, one line per finding, so an unattended log shows
    # the reason without parsing the JSON back.
    lines = captured.err.strip().splitlines()
    assert {line.split(": ")[1] for line in lines} == failed
    assert all(line.startswith("failed: ") for line in lines)
    # Including which folder and which note: a count alone names nothing to act on,
    # and each name appears once — in `paths`, not restated in the detail.
    folders = next(line for line in lines if "required_folders" in line)
    notes = next(line for line in lines if "icloud_notes" in line)
    assert folders == "failed: required_folders: 1 required folder(s) missing — Queue"
    assert "Sources/.abc.md.icloud" in notes


def test_asset_placeholder_warns_without_failing(
    healthy_vault: Path, evict: Evict, capsys: pytest.CaptureFixture[str]
) -> None:
    evict(healthy_vault / "PDFs", "abc.pdf")
    assert main(["--vault", str(healthy_vault)]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] is True
    assert out["counts"]["warning"] == 1
    assert out["counts"]["failed"] == 0
    # A warning still reaches the log, or nothing would ever surface it.
    assert captured.err.startswith("warning: icloud_assets: ")


def test_a_warning_neither_suppresses_nor_softens_a_failure(
    healthy_vault: Path, evict: Evict, capsys: pytest.CaptureFixture[str]
) -> None:
    # The two statuses co-occur on a partly-synced vault, and only the failure
    # may decide the exit code.
    evict(healthy_vault / "PDFs", "abc.pdf")
    evict(healthy_vault / "Sources", "abc.md")
    assert main(["--vault", str(healthy_vault)]) == 1
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] is False
    assert out["counts"]["warning"] == 1
    assert out["counts"]["failed"] == 1
    assert {line.split(": ")[0] for line in captured.err.strip().splitlines()} == {
        "warning",
        "failed",
    }


def test_missing_root_reports_only_that(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--vault", str(tmp_path / "nope")]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [c["name"] for c in out["checks"]] == ["vault_root"]
    # A short-circuited run reports fewer checks, not fewer keys — this is the
    # path where a reader most needs the counts to be there.
    assert out["counts"] == {"total": 1, "ok": 0, "warning": 0, "failed": 1}


def test_vault_from_environment(
    healthy_vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(healthy_vault))
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_no_vault_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    # A console script has to answer `--help` with exit 0: that is the cheapest
    # check that its entry point resolves at all, and what a caller drives it with.
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "kboat-doctor" in capsys.readouterr().out


def test_stderr_caps_a_long_path_list(
    healthy_vault: Path, evict: Evict, capsys: pytest.CaptureFixture[str]
) -> None:
    # An unsynced vault can evict thousands; one unbounded line carried into a run
    # summary as the stop reason is no more actionable than a bare count.
    for i in range(9):
        evict(healthy_vault / "Sources", f"note{i}.md")
    assert main(["--vault", str(healthy_vault)]) == 1
    captured = capsys.readouterr()
    lines = captured.err.strip().splitlines()
    line = next(one for one in lines if "icloud_notes" in one)
    assert "… and 4 more" in line
    assert line.count("Sources/.note") == MAX_REPORT_PATHS
    # The count still tells the whole truth.
    assert "9 note(s)" in line
    # The same bound applies to the JSON, which `path_count` keeps honest — both
    # streams land in the same unattended reader's context.
    entry = next(c for c in json.loads(captured.out)["checks"] if c["name"] == "icloud_notes")
    assert len(entry["paths"]) == MAX_REPORT_PATHS
    assert entry["path_count"] == 9
