"""The installed browser stack must start on the interpreter it is installed into.

``test_browser`` runs against a fake ``playwright.sync_api``, so nothing else in the
suite reaches the real ``greenlet`` that Playwright's sync API switches on. A
``greenlet`` wheel built for another build of the same CPython minor segfaults on its
first switch, which kills the process with SIGSEGV and no traceback. Each probe runs in
a subprocess for that reason: in-process, the crash would take pytest down with it
rather than fail one test.

What this covers: the ``greenlet`` extension module that is installed, and Playwright
starting and stopping its driver. What it does not cover: a Chromium launch, which needs
the browser binary that ``uv run playwright install chromium`` fetches separately.
"""

import subprocess
import sys

import pytest

_GREENLET_SWITCH = """
import greenlet
assert greenlet.greenlet(lambda: 42).switch() == 42
"""

_PLAYWRIGHT_DRIVER = """
from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
pw.stop()
"""


@pytest.mark.parametrize(
    "code",
    [_GREENLET_SWITCH, _PLAYWRIGHT_DRIVER],
    ids=["greenlet-switch", "playwright-driver"],
)
def test_browser_stack_runs_on_this_interpreter(code: str) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # A negative code is the signal that ended the process (-11 is SIGSEGV).
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr}"
