"""The `environment` check: whether the venvs the routine runs are synced to the checkout.

uv is the system boundary here, so most tests answer for it with a fake
`subprocess.run`; the last one runs the real uv against this workspace, since a
flag uv does not accept fails the check every day and no fake can see that.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from kboat.doctor import core
from kboat.doctor.core import Status, check_environment

OUTDATED = "error: The environment is outdated; run `uv sync` to update the environment"

# Taken before the doctor fixtures replace it, for the one test that runs uv.
REAL_RUN = subprocess.run

Uv = Callable[..., subprocess.CompletedProcess[str]]


def fake_uv(answers: dict[str, tuple[int, str]]) -> Uv:
    """A `subprocess.run` that answers by the project an invocation selects.

    `answers` maps a selector value (`kboat`, `tools/notebooklm`) to the exit
    code and stderr uv gives for it; unnamed ones answer as synced.
    """

    def run(args: Sequence[str], *_: object, **__: object):
        for selector, (code, stderr) in answers.items():
            if selector in args:
                return subprocess.CompletedProcess(args, code, "", stderr)
        return subprocess.CompletedProcess(args, 0, "", "Would make no changes\n")

    return run


def test_an_outdated_kboat_environment_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    run = fake_uv({"kboat": (1, f"Checked 2 packages\n{OUTDATED}\n")})
    monkeypatch.setattr(subprocess, "run", run)

    check = check_environment()

    assert check.name == "environment"
    assert check.status == Status.FAILED
    assert check.paths == ("kboat",)
    assert OUTDATED in check.detail


def test_uv_that_cannot_be_run_fails_rather_than_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    # The check runs inside the routine's gate, where a traceback names no check
    # at all; uv missing from PATH has to read as this check's finding.
    def missing(args: Sequence[str], *_: object, **__: object):
        raise FileNotFoundError(2, "No such file or directory", "uv")

    monkeypatch.setattr(subprocess, "run", missing)

    check = check_environment()

    assert check.status == Status.FAILED
    assert check.paths == ("kboat", "tools/notebooklm")
    assert "No such file or directory" in check.detail


def test_the_vault_checks_run_after_it_whatever_the_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Nothing about the vault bears on the environment, so an absent root, which
    # short-circuits every vault check after it, still leaves this one reported.
    run = fake_uv({"kboat": (1, OUTDATED)})
    monkeypatch.setattr(subprocess, "run", run)

    checks = core.run_checks(tmp_path / "nope")

    assert [(c.name, c.status) for c in checks] == [
        ("environment", Status.FAILED),
        ("vault_root", Status.FAILED),
    ]


def test_real_uv_accepts_the_arguments_and_finds_kboat_synced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A usage error is a non-zero exit like an outdated environment, so an
    # argument uv rejects would fail this check on every run. The workspace venv
    # is synced wherever this suite runs; the NotebookLM venv is not in CI, so
    # only kboat's answer is asserted.
    monkeypatch.setattr(subprocess, "run", REAL_RUN)

    check = check_environment()

    assert "kboat" not in check.paths, check.detail


def test_the_detail_carries_uvs_error_line_rather_than_its_trailing_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # uv ends a refusal with usage and a pointer to `--help`; the line saying what
    # went wrong is the one a human reading the routine's log can act on.
    stderr = "error: unexpected argument '--x' found\n\nUsage: uv sync\n\nFor more information, try '--help'.\n"
    run = fake_uv({"kboat": (2, stderr)})
    monkeypatch.setattr(subprocess, "run", run)

    assert "unexpected argument '--x' found" in check_environment().detail
