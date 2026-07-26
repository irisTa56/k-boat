#!/usr/bin/env python3
"""Fail if any `src/` file's coverage drops below a floor.

Stdlib-only by design: this script is invoked from the same `qa:py:*` gate
that keeps `kboat` a zero-runtime-dependency package, so it must not need
anything beyond what ships with Python itself.

Reads a `coverage json` report (see `coverage json --help`) and checks each
`src/` file's `summary.percent_covered` -- the same statement+branch
composite figure `coverage report` prints as "Cover", since every member
enables `branch = true`. A file at or above the floor passes; a file with no
entry in the report was never imported by the test run and is not checked,
matching what `coverage report` itself would show.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Matches the floor the user set after measuring current `main`: the lowest
# per-file figure there is 80.0% (kboat's `io_utils.py`), so the floor sits
# exactly at that measured minimum rather than below it.
DEFAULT_FLOOR = 80.0


def find_failures(report_path: Path, floor: float) -> list[tuple[str, float]]:
    """Return (file, percent_covered) pairs under `floor`, sorted by path."""
    data = json.loads(report_path.read_text(encoding="utf-8"))
    failures = [
        (file_path, info["summary"]["percent_covered"])
        for file_path, info in data["files"].items()
        # Only shipped code is held to the floor -- coverage.json is scoped to
        # `src/<package>` by each member's `[tool.coverage.run]`, but this
        # guards the invariant explicitly rather than trusting the caller.
        if file_path.startswith("src/")
        if info["summary"]["percent_covered"] < floor
    ]
    failures.sort()
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="path to a `coverage json` report")
    parser.add_argument(
        "--floor",
        type=float,
        default=DEFAULT_FLOOR,
        help=f"minimum per-file coverage percent (default: {DEFAULT_FLOOR})",
    )
    args = parser.parse_args(argv)

    if not args.report.is_file():
        print(f"coverage_floor: report not found: {args.report}", file=sys.stderr)
        return 2

    failures = find_failures(args.report, args.floor)
    if failures:
        print(
            f"coverage_floor: {len(failures)} file(s) below the {args.floor:.1f}% floor:",
            file=sys.stderr,
        )
        for file_path, percent in failures:
            print(f"  {percent:.1f}%  {file_path}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
