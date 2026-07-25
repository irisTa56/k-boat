"""Synchronous Playwright test double.

Re-derived — not copied — from loose-feeds' async fake: that double is built on
``async_api`` / ``AsyncMock``; this is a plain ``sync_api`` surface where every
method is a synchronous callable (F7). It is injected into ``sys.modules`` so the
lazy ``from playwright.sync_api import sync_playwright`` inside ``browser.get_browser``
resolves without a real Chromium, letting the whole lazy-init + fetch path run
under coverage with no ``# pragma: no cover`` glue.

A test builds a ``FakeContext`` describing one navigation outcome (rendered HTML,
a response with status/url/body, or an error to raise) and passes it to
``install_fake_playwright``; the returned handles expose what the assertions need
(``browser.new_context_kwargs`` for the UA strip, ``chromium.launch_count`` /
``browser.closed`` / ``playwright.stopped`` for the lifecycle).
"""

from __future__ import annotations

import importlib.machinery
import sys
import types
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
    ``content()`` failure.
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
        self.pages: list[FakePage] = []

    def new_page(self) -> FakePage:
        page = FakePage(self)
        self.pages.append(page)
        return page


class FakeBrowser:
    """A ``Browser`` stand-in. ``version`` feeds the UA strip."""

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
    def __init__(self, browser: FakeBrowser, launch_error: Exception | None = None) -> None:
        self._browser = browser
        self._launch_error = launch_error
        self.launch_count = 0

    def launch(self, **_: Any) -> FakeBrowser:
        self.launch_count += 1
        if self._launch_error is not None:
            raise self._launch_error
        return self._browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch, *, context: FakeContext, launch_error: Exception | None = None
) -> SimpleNamespace:
    """Inject a fake ``playwright.sync_api`` so ``browser.get_browser`` resolves to it.

    ``launch_error`` makes ``chromium.launch()`` raise it, standing in for a machine
    where the package imports but the Chromium binary will not start.

    Returns a namespace of ``playwright`` / ``chromium`` / ``browser`` / ``context``
    handles for assertions.
    """
    browser = FakeBrowser(context)
    chromium = FakeChromium(browser, launch_error)
    playwright = FakePlaywright(chromium)

    starter = SimpleNamespace(start=lambda: playwright)  # sync_playwright().start()
    fake_module = types.ModuleType("playwright.sync_api")
    # setattr (not direct assignment) so the dynamic module attribute does not trip
    # the type checker, which cannot see attributes added to a bare ModuleType.
    setattr(fake_module, "sync_playwright", lambda: starter)  # noqa: B010
    # Inject the parent package too, with a valid ``__spec__`` so the fake fully
    # represents an *installed* Playwright: ``importlib.util.find_spec("playwright")``
    # (used by ``_playwright_installed`` / the gates) returns this spec rather than
    # raising on a ``__spec__ is None`` stub. Always override, so the result does not
    # depend on whether the real dev-only ``playwright`` happens to be imported.
    fake_parent = types.ModuleType("playwright")
    fake_parent.__spec__ = importlib.machinery.ModuleSpec("playwright", loader=None)
    monkeypatch.setitem(sys.modules, "playwright", fake_parent)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)

    return SimpleNamespace(
        playwright=playwright, chromium=chromium, browser=browser, context=context
    )
