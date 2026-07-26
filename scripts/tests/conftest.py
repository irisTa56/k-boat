"""Puts `scripts/` on `sys.path` so its modules import by bare name.

`scripts/` is not a package (no `pyproject.toml`, no `src/` layout) -- it is a
flat directory of one-off tooling scripts, so there is no installed
distribution for `coverage_floor` to be importable through.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
