"""The ``rem`` wrapper — the only writer of Reminders.app (REQ-005, CON-004).

``add_reminder`` shells out to ``rem add … -o json`` and returns the created id.
Two invariants the rest of the pipeline relies on:

- **A reminder always has a non-empty title.** ``rem add`` rejects an empty name,
  so a missing/blank title falls back to the source ``url`` (REQ-005). Scrape
  keeps and error fallbacks lean on this.
- **A failed ``rem`` surfaces, never swallows.** A non-zero exit (e.g. the
  ``Filtered Feeds`` list was renamed away, CON-004) raises ``ReminderError`` so
  the caller does NOT then record the entry seen and silently lose it.

``runner`` is injected (defaults to ``subprocess.run``) so tests assert the exact
argv without touching Reminders.app. The ``Filtered Feeds`` list holds **only
pages** (article reminders, including error-noted ones); operational notices
(self-heal, per-site errors, the run summary) go to the routine's push
notification, never into the list — so there is no alert-reminder channel here.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from feed_filter.config import REMINDER_LIST

# The CLI binary. Bare name (resolved via PATH); the scheduled routine is
# responsible for a PATH that includes Homebrew (DEP-007).
REM_BINARY = "rem"

# The injected runner mirrors ``subprocess.run``'s shape: argv list in, an object
# exposing ``returncode`` / ``stdout`` / ``stderr`` out.
Runner = Callable[..., Any]


class ReminderError(Exception):
    """A non-zero ``rem`` exit or unparseable output for one ``add``.

    Carries the failing argv and captured stderr so the run summary can report
    *why* an entry could not be reminded (the list vanished, ``rem`` is missing).
    """

    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr.strip()
        detail = self.stderr or "no stderr"
        super().__init__(f"rem exited {returncode}: {detail}")


def add_reminder(
    title: str | None,
    url: str | None,
    notes: str | None,
    *,
    list_name: str = REMINDER_LIST,
    runner: Runner = subprocess.run,
) -> str:
    """Create one reminder via ``rem add`` and return its id.

    ``title`` falls back to ``url`` when blank/``None`` (REQ-005); if both are
    empty the call raises ``ValueError`` rather than shipping ``rem`` an empty
    name. ``url`` / ``notes`` are omitted from the argv when empty (so the alert
    channel can pass a bare title). A non-zero ``rem`` exit raises
    ``ReminderError`` (CON-004) — the caller must not record the entry seen.

    Arguments are passed as separate argv elements (never a shell string), so a
    title or note containing shell metacharacters is inert.
    """
    effective_title = title.strip() if title and title.strip() else (url or "").strip()
    if not effective_title:
        raise ValueError("add_reminder requires a non-empty title or url")

    argv = [REM_BINARY, "add", effective_title, "--list", list_name, "-o", "json"]
    if url:
        argv += ["--url", url]
    if notes:
        argv += ["--notes", notes]

    try:
        proc = runner(argv, capture_output=True, text=True)
    except OSError as exc:
        # rem absent from PATH or not executable. The scheduled routine inherits
        # a PATH that often lacks Homebrew (CON-001/DEP-007), so this is a likely
        # failure, not a corner case. Route it through the same channel as a
        # non-zero exit (CON-004) — an actionable error, never a raw traceback.
        # 127 is the conventional "command not found" code.
        raise ReminderError(argv, 127, f"could not execute {REM_BINARY!r}: {exc}") from exc
    if proc.returncode != 0:
        raise ReminderError(argv, proc.returncode, proc.stderr or "")
    try:
        return str(json.loads(proc.stdout)["id"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReminderError(
            argv, proc.returncode, f"unparseable rem output: {proc.stdout!r}"
        ) from exc
