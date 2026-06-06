"""Behavior tests for the ``rem`` wrapper (TASK-028).

No real Reminders.app: a fake runner captures the exact argv and returns a
canned process result, so the title-fallback, special-char safety, and
non-zero-exit-raises contracts are checked without side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from feed_filter.reminders import ReminderError, add_reminder, report


@dataclass
class FakeProc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeRunner:
    """Records every invocation and replays a fixed result."""

    proc: FakeProc
    calls: list[tuple[list[str], dict[str, Any]]] = field(default_factory=list)

    def __call__(self, argv: list[str], **kwargs: Any) -> FakeProc:
        self.calls.append((argv, kwargs))
        return self.proc


def _ok(id_: str = "ABC-123") -> FakeRunner:
    return FakeRunner(FakeProc(returncode=0, stdout=f'{{"id": "{id_}", "name": "x"}}'))


def test_exact_argv_and_id_parsed() -> None:
    runner = _ok("R1")
    rid = add_reminder("My Title", "https://e.example.com/a", "a note", runner=runner)

    assert rid == "R1"
    argv, kwargs = runner.calls[0]
    assert argv == [
        "rem",
        "add",
        "My Title",
        "--list",
        "Filtered Feeds",
        "-o",
        "json",
        "--url",
        "https://e.example.com/a",
        "--notes",
        "a note",
    ]
    # Captured and decoded so returncode/stdout are inspectable; never via a shell.
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_special_chars_passed_as_single_argv_elements() -> None:
    # Shell metacharacters in a title/note must travel as inert argv elements,
    # not be word-split or interpreted (no shell=True anywhere).
    runner = _ok()
    title = 'A; rm -rf / & "$(whoami)"'
    notes = "line with | pipe and `backtick`"
    add_reminder(title, "https://e.example.com/a", notes, runner=runner)

    argv = runner.calls[0][0]
    assert argv[2] == title
    assert argv[argv.index("--notes") + 1] == notes


def test_empty_title_falls_back_to_url() -> None:
    runner = _ok()
    add_reminder("   ", "https://e.example.com/fallback", None, runner=runner)
    argv = runner.calls[0][0]
    assert argv[2] == "https://e.example.com/fallback"
    # notes omitted when None.
    assert "--notes" not in argv


def test_none_title_falls_back_to_url() -> None:
    runner = _ok()
    add_reminder(None, "https://e.example.com/x", None, runner=runner)
    assert runner.calls[0][0][2] == "https://e.example.com/x"


def test_no_title_and_no_url_raises() -> None:
    runner = _ok()
    with pytest.raises(ValueError, match="non-empty title or url"):
        add_reminder("", "", None, runner=runner)
    assert runner.calls == []  # never invoked rem with an empty name


def test_nonzero_exit_raises_unknown_list() -> None:
    # CON-004: a renamed/missing list must surface, not swallow the kept entry.
    runner = FakeRunner(FakeProc(returncode=1, stderr="reminders: list not found: Filtered Feeds"))
    with pytest.raises(ReminderError) as excinfo:
        add_reminder("T", "https://e.example.com/a", "n", runner=runner)
    assert excinfo.value.returncode == 1
    assert "list not found" in excinfo.value.stderr


def test_unparseable_output_raises() -> None:
    runner = FakeRunner(FakeProc(returncode=0, stdout="not json"))
    with pytest.raises(ReminderError, match="unparseable"):
        add_reminder("T", "https://e.example.com/a", "n", runner=runner)


def test_missing_binary_raises_reminder_error() -> None:
    # rem absent from PATH (the likely scheduled-routine failure, CON-001/DEP-007)
    # must surface as a ReminderError, not a raw OSError traceback (CON-004).
    def boom(*_a: Any, **_k: Any) -> FakeProc:
        raise FileNotFoundError(2, "No such file or directory", "rem")

    with pytest.raises(ReminderError) as excinfo:
        add_reminder("T", "https://e.example.com/a", "n", runner=boom)
    assert excinfo.value.returncode == 127
    assert "could not execute" in str(excinfo.value)


def test_report_formats_alert() -> None:
    runner = _ok("ALERT-1")
    rid = report("healed s1: pattern changed", runner=runner)
    assert rid == "ALERT-1"
    argv = runner.calls[0][0]
    assert argv[2] == "feed-filter: healed s1: pattern changed"
    # Alerts carry no url/notes.
    assert "--url" not in argv
    assert "--notes" not in argv
