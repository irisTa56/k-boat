"""Synchronous Playwright test double (TASK-008).

Re-derived — not copied — from loose-feeds' async fake: that double is built on
``async_api`` / ``AsyncMock``; this is a plain ``sync_api`` surface where every
method is a synchronous callable (F7). It is injected into ``sys.modules`` so the
lazy ``from playwright.sync_api import sync_playwright`` inside ``browser.get_browser``
resolves without a real Chromium, letting the whole lazy-init + fetch path run
under coverage with no ``# pragma: no cover`` glue.

A test builds a ``FakeContext`` describing one navigation outcome (rendered HTML,
a response with status/url/body, or an error to raise) and passes it to
``install_fake_playwright``; the returned handles expose what the assertions need
(``browser.new_context_kwargs`` for the UA strip, ``context.storage_state_calls``
and ``chromium.launch_count`` / ``playwright.stopped`` for the lifecycle).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


class FakeResponse:
    """A ``playwright.sync_api.Response`` stand-in for one navigation."""

    def __init__(
        self,
        *,
        status: int = 200,
        url: str = "https://example.com/",
        body: bytes = b"",
        body_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.url = url
        self._body = body
        self._body_error = body_error

    def body(self) -> bytes:
        if self._body_error is not None:
            raise self._body_error
        return self._body


class FakePage:
    """A ``Page`` stand-in: ``goto`` returns the context's configured response."""

    def __init__(self, context: FakeContext) -> None:
        self._context = context
        self.closed = False

    def goto(
        self, url: str, *, wait_until: str | None = None, timeout: float | None = None
    ) -> FakeResponse | None:
        self._context.goto_calls.append(
            SimpleNamespace(url=url, wait_until=wait_until, timeout=timeout)
        )
        if self._context.goto_error is not None:
            raise self._context.goto_error
        return self._context.response

    def content(self) -> str:
        if self._context.content_error is not None:
            raise self._context.content_error
        return self._context.html

    def close(self) -> None:
        self.closed = True


class FakeContext:
    """A ``BrowserContext`` stand-in describing one navigation outcome.

    ``response`` may be a ``FakeResponse`` or ``None`` (same-document nav). Set
    ``goto_error`` / ``content_error`` to simulate a navigation/timeout or a
    ``content()`` failure. ``storage_state(path=...)`` records the call and writes
    a stub file so the next boot's ``state_path.exists()`` check would see it.
    """

    def __init__(
        self,
        *,
        html: str = "",
        response: FakeResponse | None = None,
        goto_error: Exception | None = None,
        content_error: Exception | None = None,
    ) -> None:
        self.html = html
        self.response = response
        self.goto_error = goto_error
        self.content_error = content_error
        self.goto_calls: list[SimpleNamespace] = []
        self.storage_state_calls: list[str] = []
        self.pages: list[FakePage] = []

    def new_page(self) -> FakePage:
        page = FakePage(self)
        self.pages.append(page)
        return page

    def storage_state(self, *, path: str) -> dict[str, Any]:
        self.storage_state_calls.append(path)
        Path(path).write_text('{"cookies": []}', encoding="utf-8")
        return {"cookies": []}


class FakeBrowser:
    """A ``Browser`` stand-in. ``version`` feeds the UA strip (REQ-005)."""

    # A real Chromium version string; the exact value is irrelevant, only that the
    # attribute exists and flows into the UA the strip rebuilds (F1/F7).
    version = "147.0.7727.15"

    def __init__(self, context: FakeContext) -> None:
        self._context = context
        self.closed = False
        self.new_context_kwargs: dict[str, Any] | None = None

    def new_context(self, **kwargs: Any) -> FakeContext:
        self.new_context_kwargs = kwargs
        return self._context

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self._browser = browser
        self.launch_count = 0

    def launch(self, **_: Any) -> FakeBrowser:
        self.launch_count += 1
        return self._browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch, *, context: FakeContext
) -> SimpleNamespace:
    """Inject a fake ``playwright.sync_api`` so ``browser.get_browser`` resolves to it.

    Returns a namespace of ``playwright`` / ``chromium`` / ``browser`` / ``context``
    handles for assertions.
    """
    browser = FakeBrowser(context)
    chromium = FakeChromium(browser)
    playwright = FakePlaywright(chromium)

    starter = SimpleNamespace(start=lambda: playwright)  # sync_playwright().start()
    fake_module = types.ModuleType("playwright.sync_api")
    # setattr (not direct assignment) so the dynamic module attribute does not trip
    # the type checker, which cannot see attributes added to a bare ModuleType.
    setattr(fake_module, "sync_playwright", lambda: starter)  # noqa: B010
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    # The parent package must resolve for ``from playwright.sync_api import ...``.
    if "playwright" not in sys.modules:
        monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))

    return SimpleNamespace(
        playwright=playwright, chromium=chromium, browser=browser, context=context
    )
