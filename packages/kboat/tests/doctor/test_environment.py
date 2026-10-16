"""The `environment` check, with uv faked except in the one test that runs it."""

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
    assert "kboat" not in check.detail  # names live in `paths` alone


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (FileNotFoundError(2, "No such file or directory", "uv"), "No such file or directory"),
        (subprocess.TimeoutExpired(["uv"], 60), "timed out after 60 seconds"),
    ],
)
def test_uv_that_cannot_be_run_fails_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch, error: Exception, said: str
) -> None:
    # A traceback in the routine's gate names no check at all.
    def broken(*_: object, **__: object):
        raise error

    monkeypatch.setattr(subprocess, "run", broken)

    check = check_environment()

    assert check.status == Status.FAILED
    assert check.paths == ("kboat", "tools/notebooklm")
    assert said in check.detail


def test_the_vault_checks_run_after_it_whatever_the_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An absent root short-circuits the vault checks, not this one.
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
    # An argument uv rejects would fail the check on every run. CI has no
    # NotebookLM venv, so only kboat's answer is asserted.
    monkeypatch.setattr(subprocess, "run", REAL_RUN)

    check = check_environment()

    assert "kboat" not in check.paths, check.detail


def test_the_detail_carries_uvs_error_line_rather_than_its_trailing_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = "error: unexpected argument '--x' found\n\nUsage: uv sync\n\nFor more information, try '--help'.\n"
    run = fake_uv({"kboat": (2, stderr)})
    monkeypatch.setattr(subprocess, "run", run)

    assert "unexpected argument '--x' found" in check_environment().detail


def test_every_invocation_is_bounded_and_asks_about_this_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A synced checkout run from its root answers the same without any of these,
    # so the real-uv test cannot see one dropped: `--no-dev` keeps a stale dev
    # tool from stopping the routine, `--offline` and the timeout keep the network
    # and a hung uv out of the gate, and `cwd` is what makes the checkout the one
    # this module sits in rather than the caller's.
    seen: list[tuple[Sequence[str], dict[str, object]]] = []

    def run(args: Sequence[str], **kwargs: object):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(Path("/"))

    check_environment()

    root = Path(__file__).resolve().parents[4]  # this checkout's root
    assert len(seen) == 2
    for args, kwargs in seen:
        assert {"--no-dev", "--offline"} <= set(args)
        assert kwargs["cwd"] == root
        assert kwargs["timeout"]
