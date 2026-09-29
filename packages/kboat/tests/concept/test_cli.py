"""End-to-end tests for the `kboat-concept` CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kboat.concept.__main__ import main

GROUPED_NOTE = (
    "## Observations\n\n### A group\n\n- [x] y\n\n## Relations\n\n- related_to [[Other]]\n"
)
FLAT_NOTE = "## Observations\n\n- [x] y\n\n## Relations\n\n- related_to [[Other]]\n"
FILE_PATH = "concepts/A note.md"


def _knowledge(tmp_path: Path, note: str | bytes) -> Path:
    note_path = tmp_path / FILE_PATH
    note_path.parent.mkdir()
    if isinstance(note, bytes):
        note_path.write_bytes(note)
    else:
        note_path.write_text(note, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(("note", "shape"), [(GROUPED_NOTE, "grouped"), (FLAT_NOTE, "flat")])
def test_shape_reports_the_note_at_the_path(
    note: str, shape: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Both values asserted as the literals the record publishes, not through the
    # module constants -- a constant and every assertion against it move together,
    # so a rename of either value would ship green and reach a writer that branches
    # on the literal.
    root = _knowledge(tmp_path, note)
    assert main(["--knowledge", str(root), "shape", FILE_PATH]) == 0
    assert json.loads(capsys.readouterr().out) == {"shape": shape}


def test_root_defaults_to_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The form kboat-distill calls: no root on the command line, so that the one
    # Bash rule `kboat-concept shape:*` covers it with no absolute path in it.
    monkeypatch.setenv("KBOAT_KNOWLEDGE_PATH", str(_knowledge(tmp_path, GROUPED_NOTE)))
    assert main(["shape", FILE_PATH]) == 0
    assert json.loads(capsys.readouterr().out) == {"shape": "grouped"}


@pytest.mark.parametrize("note", ["", "# Error\n\nNote not found\n"])
def test_text_that_is_not_a_concept_note_is_refused(
    note: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing on stdout: a caller that parsed a record here would be reading an
    # answer about a note the tool never saw.
    root = _knowledge(tmp_path, note)
    # Exit 2, not 1: `kboat.cli` reserves 2 for a record the caller has to fix, and
    # 1 for an operation that did not happen for a reason outside them -- the shape
    # the routine prompt reads as a vault lock it cannot operate.
    assert main(["--knowledge", str(root), "shape", FILE_PATH]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    # The opening clause verbatim: argparse's usage error exits 2 with an empty
    # stdout too, so this line is the only thing that says which of the two a
    # caller met. A reword that kept only "## Observations" would ship green and
    # leave a reader of the stderr no way to tell them apart.
    assert captured.err.startswith("no concept note at ")
    assert "## Observations" in captured.err


def test_a_missing_note_is_the_callers_to_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A path Basic Memory did not return, or a root set to the wrong directory: both
    # the caller's to correct, so exit 2 and not the 1 of a read the OS refused.
    assert main(["--knowledge", str(tmp_path), "shape", FILE_PATH]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("no such note: ")


def test_a_read_the_os_refuses_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _knowledge(tmp_path, GROUPED_NOTE)
    note_path = root / FILE_PATH
    note_path.chmod(0)
    try:
        if os.access(note_path, os.R_OK):
            pytest.skip("running with privileges that ignore file permissions")
        assert main(["--knowledge", str(root), "shape", FILE_PATH]) == 1
    finally:
        note_path.chmod(0o644)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("could not read ")


def test_a_note_that_does_not_decode_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _knowledge(tmp_path, b"## Observations\n\n\xff\n")
    assert main(["--knowledge", str(root), "shape", FILE_PATH]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("could not read ")


def test_no_root_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KBOAT_KNOWLEDGE_PATH", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        main(["shape", FILE_PATH])
    assert excinfo.value.code == 2


@pytest.mark.parametrize("argv", [[], ["shape"]])
def test_no_subcommand_or_path_is_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == 2
