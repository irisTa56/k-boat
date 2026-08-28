"""End-to-end tests for the `kboat-concept` CLI."""

from __future__ import annotations

import io
import json

import pytest

from kboat.concept.__main__ import main

GROUPED_NOTE = (
    "## Observations\n\n### A group\n\n- [x] y\n\n## Relations\n\n- related_to [[Other]]\n"
)
FLAT_NOTE = "## Observations\n\n- [x] y\n\n## Relations\n\n- related_to [[Other]]\n"


def _stdin(stdin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))


@pytest.mark.parametrize(("note", "shape"), [(GROUPED_NOTE, "grouped"), (FLAT_NOTE, "flat")])
def test_shape_reports_the_note_on_stdin(
    note: str, shape: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Both values asserted as the literals the record publishes, not through the
    # module constants -- a constant and every assertion against it move together,
    # so a rename of either value would ship green and reach a writer that branches
    # on the literal.
    _stdin(note, monkeypatch)
    assert main(["shape"]) == 0
    assert json.loads(capsys.readouterr().out) == {"shape": shape}


@pytest.mark.parametrize("stdin", ["", "# Error\n\nNote not found\n"])
def test_text_that_is_not_a_concept_note_is_refused(
    stdin: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing on stdout: a caller that parsed a record here would be reading an
    # answer about a note the tool never saw. An omitted `< <tmpfile>` redirect
    # hands the process an empty stdin, so it is this exit that catches it.
    _stdin(stdin, monkeypatch)
    # Exit 2, not 1: `kboat.cli` reserves 2 for a record the caller has to fix, and
    # 1 for an operation that did not happen for a reason outside them -- the shape
    # the routine prompt reads as a vault lock it cannot operate.
    assert main(["shape"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    # The opening clause verbatim: argparse's usage error exits 2 with an empty
    # stdout too, so this line is the only thing that says which of the two a
    # caller met. A reword that kept only "## Observations" would ship green and
    # leave a reader of the stderr no way to tell them apart.
    assert captured.err.startswith("no concept note on stdin")
    assert "## Observations" in captured.err


def test_no_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
