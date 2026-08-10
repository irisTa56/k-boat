"""Fail if any `src/` file's coverage drops below a fixed floor.

Stdlib-only by design: this script is invoked from the same `qa:py:*` gate
that keeps `kboat` a zero-runtime-dependency package, so it must not need
anything beyond what ships with Python itself.

Reads a `coverage json` report -- the JSON format `coverage`/pytest-cov write
(each member's `pytest` produces it directly via `--cov-report=json` in its
`addopts`) -- and checks each `src/` file's `summary.percent_covered`, the
same statement+branch composite figure `coverage report` prints as "Cover",
since every member enables `branch = true`. A file at or above the floor
passes. Each member's `pytest` run scopes coverage to its own `src/<package>`
(`--cov=<module>`), so `coverage` walks that whole directory rather than only
recording modules the tests happened to import -- a file that exists but was
never imported still gets an entry, at 0%, not omitted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# A floor rather than a running measurement: it says how far one `src/` file may
# fall before the gate refuses, not how well covered the package is today. It is
# the user's to raise, and raising it is a policy decision rather than
# bookkeeping -- so it does not track the measured minimum, and the slack between
# the two is deliberate rather than drift. Not a CLI flag either, for the same
# reason: a caller must not be able to loosen it.
FLOOR = 80.0

# Any malformed-report shape lands here (e.g. a `files` entry missing
# `summary.percent_covered`, or a non-numeric one) -- exit 2, "the input isn't
# what this script expects", never exit 1, "a file is below the floor".
_MALFORMED_REPORT_ERRORS = (KeyError, TypeError)


def checked_files(report: dict) -> list[str]:
    """Return the `src/`-scoped file paths a floor check examines.

    An empty result means nothing was checked -- either the report has no
    shipped code in it, or its keys use a different prefix than expected
    (e.g. `pytest` ran from an unexpected working directory). Either way, the
    caller must treat that as an error, not a vacuous pass.
    """
    return [file_path for file_path in report["files"] if file_path.startswith("src/")]


def find_failures(report: dict, files: list[str] | None = None) -> list[tuple[str, float]]:
    """Return (file, percent_covered) pairs under `FLOOR`, sorted by path.

    `files` lets a caller that already has `checked_files(report)` pass it
    through instead of recomputing it; defaults to computing it fresh.
    """
    if files is None:
        files = checked_files(report)
    failures = []
    for file_path in files:
        percent = report["files"][file_path]["summary"]["percent_covered"]
        if percent < FLOOR:
            failures.append((file_path, percent))
    failures.sort()
    return failures


def _load_report(report_path: Path) -> dict | None:
    """Parse `report_path` as a `coverage json` report, or print why not."""
    try:
        text = report_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"coverage_floor: {report_path} is not UTF-8 text: {exc}", file=sys.stderr)
        return None
    except OSError as exc:
        # Covers the gap between the caller's `is_file()` check and this read
        # (permissions changed, the file was removed) -- same "can't read the
        # input" class as the UTF-8 case above, not "a file is below the floor".
        print(f"coverage_floor: cannot read {report_path}: {exc}", file=sys.stderr)
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"coverage_floor: {report_path} is not valid JSON: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        print(
            f"coverage_floor: {report_path} has no top-level `files` object -- "
            "not a `coverage json` report",
            file=sys.stderr,
        )
        return None
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("report", type=Path, help="path to a `coverage json` report")
    args = parser.parse_args(argv)

    if not args.report.is_file():
        print(f"coverage_floor: report not found: {args.report}", file=sys.stderr)
        return 2

    report = _load_report(args.report)
    if report is None:
        return 2

    files = checked_files(report)
    if not files:
        print(
            "coverage_floor: no `src/`-prefixed file found in the report; "
            "check the working directory `pytest` ran from",
            file=sys.stderr,
        )
        return 2

    try:
        failures = find_failures(report, files)
    except _MALFORMED_REPORT_ERRORS as exc:
        print(
            f"coverage_floor: {args.report} does not match the expected "
            f"`coverage json` shape ({exc!r})",
            file=sys.stderr,
        )
        return 2

    if failures:
        print(
            f"coverage_floor: {len(failures)} file(s) below the {FLOOR:.1f}% floor:",
            file=sys.stderr,
        )
        for file_path, percent in failures:
            print(f"  {percent:.2f}%  {file_path}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
