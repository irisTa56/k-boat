"""Smoke-test every console script this package publishes.

Reads the script list from `pyproject.toml` instead of hardcoding it, so a
new `[project.scripts]` entry is covered automatically without a matching
test edit -- the failure mode this guards against is a broken entry point
(a typo'd module path, a missing import at call time), not the depth its own
unit tests already cover.

Intentionally duplicated near-verbatim in the sibling `feed-filter` member
rather than shared: each package is tested as a self-contained unit, with no
test-time import from the other, and the two entry-point lists are unrelated.
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
    # Guards against the parametrized test below going vacuous if
    # `[project.scripts]` were ever emptied out. (A renamed or removed
    # `[project.scripts]` *table* fails at collection instead, since
    # `_console_script_names()` runs at import time to build the
    # `parametrize` list -- this only catches the table being present but
    # empty.)
    assert _console_script_names()


@pytest.mark.parametrize("script_name", _console_script_names())
def test_entry_point_help_exits_zero(script_name: str) -> None:
    # The installed script, not `python -m`: this exercises the actual
    # console-script shim `uv sync` generated, matching how a user invokes it.
    # Relies on the workspace venv's `bin/` being on `PATH` (true under
    # `uv run`, which this test itself always runs under).
    try:
        result = subprocess.run(
            [script_name, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,  # the assertion below is the check, and it reports the output
        )
    except FileNotFoundError:
        pytest.fail(
            f"{script_name!r} is not on PATH -- run `uv sync` to (re)install "
            "the workspace venv's console-script shims"
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{script_name!r} --help did not exit within 10s")
    assert result.returncode == 0, result.stdout + result.stderr
