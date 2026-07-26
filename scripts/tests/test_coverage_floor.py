"""Tests for `scripts/coverage_floor.py`.

Exercises the pure `checked_files` / `find_failures` functions directly (no
subprocess), plus `main` end to end against a temp report file, since `main`
is what the `qa:py:*` gate actually invokes. `conftest.py` puts `scripts/` on
`sys.path` so the import below resolves.
"""

from __future__ import annotations

import os
from pathlib import Path

import coverage_floor
import pytest


def _report(files: dict[str, float]) -> dict:
    return {
        "files": {
            file_path: {"summary": {"percent_covered": percent}}
            for file_path, percent in files.items()
        }
    }


def test_checked_files_keeps_only_src_prefixed_entries() -> None:
    report = _report({"src/kboat/a.py": 100.0, "tests/test_a.py": 0.0})
    assert coverage_floor.checked_files(report) == ["src/kboat/a.py"]


def test_checked_files_empty_when_nothing_matches_the_prefix() -> None:
    # The case the floor must not pass silently on: a report whose keys don't
    # start with `src/` at all (e.g. `pytest` ran from an unexpected working
    # directory).
    report = _report({"packages/kboat/src/kboat/a.py": 50.0})
    assert coverage_floor.checked_files(report) == []


def test_find_failures_returns_only_files_under_the_floor() -> None:
    report = _report(
        {
            "src/pkg/below.py": coverage_floor.FLOOR - 0.1,
            "src/pkg/at_floor.py": coverage_floor.FLOOR,
            "src/pkg/above.py": 100.0,
        }
    )
    assert coverage_floor.find_failures(report) == [
        ("src/pkg/below.py", coverage_floor.FLOOR - 0.1)
    ]


def test_find_failures_sorted_by_path() -> None:
    report = _report({"src/pkg/z.py": 0.0, "src/pkg/a.py": 0.0})
    assert [path for path, _ in coverage_floor.find_failures(report)] == [
        "src/pkg/a.py",
        "src/pkg/z.py",
    ]


def test_main_returns_2_when_report_file_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-report.json"
    assert coverage_floor.main([str(missing)]) == 2


def test_main_returns_2_when_no_src_file_is_in_the_report(tmp_path: Path) -> None:
    report_path = tmp_path / "coverage.json"
    report_path.write_text(
        '{"files": {"tests/test_a.py": {"summary": {"percent_covered": 100.0}}}}'
    )
    assert coverage_floor.main([str(report_path)]) == 2


def test_main_returns_1_when_a_file_is_below_the_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_path = tmp_path / "coverage.json"
    report_path.write_text('{"files": {"src/pkg/a.py": {"summary": {"percent_covered": 10.0}}}}')
    assert coverage_floor.main([str(report_path)]) == 1
    captured = capsys.readouterr()
    assert "src/pkg/a.py" in captured.err


def test_main_returns_0_when_every_file_meets_the_floor(tmp_path: Path) -> None:
    report_path = tmp_path / "coverage.json"
    report_path.write_text('{"files": {"src/pkg/a.py": {"summary": {"percent_covered": 100.0}}}}')
    assert coverage_floor.main([str(report_path)]) == 0


def test_main_returns_2_on_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report_path = tmp_path / "coverage.json"
    report_path.write_text("not json")
    assert coverage_floor.main([str(report_path)]) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_main_returns_2_when_files_key_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_path = tmp_path / "coverage.json"
    report_path.write_text('{"totals": {}}')
    assert coverage_floor.main([str(report_path)]) == 2
    assert "no top-level `files` object" in capsys.readouterr().err


def test_main_returns_2_when_files_is_not_an_object(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_path = tmp_path / "coverage.json"
    report_path.write_text('{"files": ["not", "an", "object"]}')
    assert coverage_floor.main([str(report_path)]) == 2
    assert "no top-level `files` object" in capsys.readouterr().err


def test_main_returns_2_on_non_utf8_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_path = tmp_path / "coverage.json"
    report_path.write_bytes(b"\xff\xfe\x00\x01")
    assert coverage_floor.main([str(report_path)]) == 2
    assert "not UTF-8" in capsys.readouterr().err


def test_main_returns_2_when_a_src_entry_has_no_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Distinct from "below the floor" (exit 1): the report itself doesn't
    # match the shape this script expects, so it must not exit 1 and print as
    # though it found a real coverage regression. `KeyError` arm of
    # `_MALFORMED_REPORT_ERRORS`.
    report_path = tmp_path / "coverage.json"
    report_path.write_text('{"files": {"src/pkg/a.py": {}}}')
    assert coverage_floor.main([str(report_path)]) == 2
    assert "does not match the expected" in capsys.readouterr().err


def test_main_returns_2_when_percent_covered_is_not_a_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `TypeError` arm of `_MALFORMED_REPORT_ERRORS`: `"80" < FLOOR` raises,
    # rather than silently comparing as if it were a real regression.
    report_path = tmp_path / "coverage.json"
    report_path.write_text('{"files": {"src/pkg/a.py": {"summary": {"percent_covered": "80"}}}}')
    assert coverage_floor.main([str(report_path)]) == 2
    assert "does not match the expected" in capsys.readouterr().err


@pytest.mark.skipif(os.getuid() == 0, reason="root ignores file permission bits")
def test_main_returns_2_when_report_is_unreadable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_path = tmp_path / "coverage.json"
    report_path.write_text('{"files": {}}')
    report_path.chmod(0o000)
    try:
        assert coverage_floor.main([str(report_path)]) == 2
        assert "cannot read" in capsys.readouterr().err
    finally:
        report_path.chmod(0o644)  # tmp_path cleanup needs to remove the file
