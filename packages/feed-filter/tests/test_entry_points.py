"""Smoke-test every console script this package publishes.

Reads the script list from `pyproject.toml` instead of hardcoding it, so a
new `[project.scripts]` entry is covered automatically without a matching
test edit -- the failure mode this guards against is a broken entry point
(a typo'd module path, a missing import at call time), not the depth its own
unit tests already cover.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _console_script_names() -> list[str]:
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return sorted(data["project"]["scripts"])


def test_console_scripts_are_declared() -> None:
    # Guards against the parametrized test below going vacuous (e.g. a
    # `[project.scripts]` rename that `_console_script_names` no longer sees).
    assert _console_script_names()


@pytest.mark.parametrize("script_name", _console_script_names())
def test_entry_point_help_exits_zero(script_name: str) -> None:
    # The installed script, not `python -m`: this exercises the actual
    # console-script shim `uv sync` generated, matching how a user invokes it.
    result = subprocess.run(
        [script_name, "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
