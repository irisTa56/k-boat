"""Behavior tests for the sync browser module (TASK-008).

No real Chromium: a fake ``playwright.sync_api`` is injected via
``tests/_fake_playwright.py`` so the lazy-init, fetch, lifecycle, UA-strip, and
startup-gate paths all run under coverage. The module-global bundle is reset
around every test so the lazy singleton starts cold each time.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from _fake_playwright import FakeContext, FakeResponse, install_fake_playwright

from feed_filter import browser
from feed_filter.browser import (
    BrowserFetchError,
    MissingPlaywrightError,
    close_browser,
    fetch_html,
    fetch_raw,
    get_browser,
    require_playwright_if_needed,
)


@pytest.fixture(autouse=True)
def _reset_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start each test with a cold singleton and a tmp browser-state path."""
    monkeypatch.setenv("FEED_FILTER_BROWSER_STATE", str(tmp_path / "state.json"))
    browser._bundle = None
    yield
    browser._bundle = None


# --- fetch_html -----------------------------------------------------------


def test_fetch_html_returns_post_redirect_url_and_html(monkeypatch: pytest.MonkeyPatch) -> None:
    # The base is the POST-redirect URL, not the requested one, so relative links
    # resolve against where the page actually landed (symmetric with fetch_raw).
    ctx = FakeContext(
        html="<html>rendered</html>",
        response=FakeResponse(status=200, url="https://www.example.com/index"),
    )
    install_fake_playwright(monkeypatch, context=ctx)

    base_url, html = fetch_html("https://example.com/index")
    assert html == "<html>rendered</html>"
    assert base_url == "https://www.example.com/index"  # post-redirect (www), not requested
    # Navigation waits for ``load`` and passes the timeout in milliseconds.
    assert ctx.goto_calls[0].wait_until == "load"
    assert ctx.goto_calls[0].timeout == browser.DEFAULT_BROWSER_TIMEOUT_SEC * 1000.0
    # The page is closed even on the happy path (finally).
    assert ctx.pages[0].closed is True


def test_fetch_html_none_response_falls_back_to_requested_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A same-document navigation returns no response object; fetch_html skips the
    # status check, returns the rendered DOM, and bases on the requested URL.
    ctx = FakeContext(html="<html>ok</html>", response=None)
    install_fake_playwright(monkeypatch, context=ctx)
    base_url, html = fetch_html("https://example.com/index")
    assert html == "<html>ok</html>"
    assert base_url == "https://example.com/index"


def test_fetch_html_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(html="ignored", response=FakeResponse(status=403))
    install_fake_playwright(monkeypatch, context=ctx)
    with pytest.raises(BrowserFetchError, match="HTTP 403"):
        fetch_html("https://example.com/index")
    assert ctx.pages[0].closed is True


def test_fetch_html_nav_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(goto_error=RuntimeError("Timeout 20000ms exceeded"))
    install_fake_playwright(monkeypatch, context=ctx)
    with pytest.raises(BrowserFetchError, match="navigation failed"):
        fetch_html("https://example.com/index")
    assert ctx.pages[0].closed is True


def test_fetch_html_content_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(response=FakeResponse(status=200), content_error=RuntimeError("detached"))
    install_fake_playwright(monkeypatch, context=ctx)
    with pytest.raises(BrowserFetchError, match="content\\(\\) failed"):
        fetch_html("https://example.com/index")
    assert ctx.pages[0].closed is True  # finally closes the page on a mid-fetch error


def test_fetch_html_oversize_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser, "_MAX_BROWSER_BODY_BYTES", 8)
    ctx = FakeContext(html="x" * 20, response=FakeResponse(status=200))
    install_fake_playwright(monkeypatch, context=ctx)
    with pytest.raises(BrowserFetchError, match="exceeds 8 bytes"):
        fetch_html("https://example.com/index")


# --- fetch_raw ------------------------------------------------------------


def test_fetch_raw_returns_post_redirect_url_and_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = FakeResponse(status=200, url="https://example.com/feed.xml?canonical", body=b"<rss/>")
    ctx = FakeContext(response=resp)
    install_fake_playwright(monkeypatch, context=ctx)

    url, body = fetch_raw("https://example.com/feed.xml")
    assert url == "https://example.com/feed.xml?canonical"  # post-redirect URL
    assert body == b"<rss/>"
    assert ctx.pages[0].closed is True


def test_fetch_raw_nav_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(goto_error=RuntimeError("Timeout 20000ms exceeded"))
    install_fake_playwright(monkeypatch, context=ctx)
    with pytest.raises(BrowserFetchError, match="navigation failed"):
        fetch_raw("https://example.com/feed.xml")
    assert ctx.pages[0].closed is True


def test_fetch_raw_none_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(response=None)
    install_fake_playwright(monkeypatch, context=ctx)
    with pytest.raises(BrowserFetchError, match="no navigation response"):
        fetch_raw("https://example.com/feed.xml")


def test_fetch_raw_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(response=FakeResponse(status=503))
    install_fake_playwright(monkeypatch, context=ctx)
    with pytest.raises(BrowserFetchError, match="HTTP 503"):
        fetch_raw("https://example.com/feed.xml")


def test_fetch_raw_body_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(response=FakeResponse(status=200, body_error=RuntimeError("driver gone")))
    install_fake_playwright(monkeypatch, context=ctx)
    with pytest.raises(BrowserFetchError, match="body\\(\\) failed"):
        fetch_raw("https://example.com/feed.xml")
    assert ctx.pages[0].closed is True  # finally closes the page on a mid-fetch error


def test_fetch_raw_oversize_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser, "_MAX_BROWSER_BODY_BYTES", 4)
    ctx = FakeContext(response=FakeResponse(status=200, body=b"0123456789"))
    install_fake_playwright(monkeypatch, context=ctx)
    with pytest.raises(BrowserFetchError, match="exceeds 4 bytes"):
        fetch_raw("https://example.com/feed.xml")


# --- lazy singleton + lifecycle -------------------------------------------


def test_lazy_singleton_launches_once(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(html="<html/>", response=FakeResponse(status=200))
    handles = install_fake_playwright(monkeypatch, context=ctx)

    first = get_browser()
    second = get_browser()
    assert first is second
    assert handles.chromium.launch_count == 1  # one Chromium for the whole process


def test_close_browser_persists_state_and_tears_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = FakeContext(html="<html/>", response=FakeResponse(status=200))
    handles = install_fake_playwright(monkeypatch, context=ctx)

    get_browser()
    close_browser()

    state_file = tmp_path / "state.json"
    assert ctx.storage_state_calls == [str(state_file)]  # cookies persisted (REQ-004)
    assert state_file.exists()  # written to disk for the next boot
    assert handles.browser.closed is True
    assert handles.playwright.stopped is True
    # The slot is cleared, so a second close is a no-op and the next get relaunches.
    assert browser._bundle is None


def test_close_browser_is_noop_when_never_launched() -> None:
    # The common (httpx-only) run pays nothing on teardown; double-close is safe.
    close_browser()
    close_browser()


# --- storage-state cold/warm boot -----------------------------------------


def test_cold_boot_passes_no_storage_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # No state file on a clean machine → storage_state=None (Playwright raises on a
    # missing path); the file appears only after the first close writes cookies (F4).
    ctx = FakeContext(html="<html/>", response=FakeResponse(status=200))
    handles = install_fake_playwright(monkeypatch, context=ctx)

    get_browser()
    assert handles.browser.new_context_kwargs is not None
    assert handles.browser.new_context_kwargs["storage_state"] is None


def test_warm_boot_loads_existing_storage_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"cookies": []}', encoding="utf-8")
    ctx = FakeContext(html="<html/>", response=FakeResponse(status=200))
    handles = install_fake_playwright(monkeypatch, context=ctx)

    get_browser()
    assert handles.browser.new_context_kwargs["storage_state"] == str(state_file)


# --- UA strip (REQ-005) ---------------------------------------------------


def test_ua_strip_removes_headless_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(html="<html/>", response=FakeResponse(status=200))
    handles = install_fake_playwright(monkeypatch, context=ctx)

    get_browser()
    ua = handles.browser.new_context_kwargs["user_agent"]
    assert "Headless" not in ua  # the load-bearing Cloudflare detail
    assert handles.browser.version in ua  # rebuilt from the live version, not hard-coded


# --- require_playwright_if_needed (startup gate) ---------------------------


def _write_sites(tmp_path: Path, *, flagged: bool) -> Path:
    path = tmp_path / "sites.toml"
    flag = "requires_browser = true\n" if flagged else ""
    path.write_text(
        '[[site]]\nid = "ex"\nname = "Ex"\nfeed_url = "https://e.example.com/f.xml"\n' + flag,
        encoding="utf-8",
    )
    return path


def test_require_playwright_missing_file_is_noop(tmp_path: Path) -> None:
    # A fresh clone with no sites.toml must not require the extra.
    require_playwright_if_needed(tmp_path / "absent.toml")


def test_require_playwright_no_flagged_sites_is_noop(tmp_path: Path) -> None:
    require_playwright_if_needed(_write_sites(tmp_path, flagged=False))


def test_require_playwright_flagged_but_missing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        browser.importlib.util,
        "find_spec",
        lambda name: None if name == "playwright" else object(),
    )
    with pytest.raises(MissingPlaywrightError, match="uv sync --extra browser"):
        require_playwright_if_needed(_write_sites(tmp_path, flagged=True))


def test_require_playwright_flagged_and_present_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Counterpart to the missing test: when find_spec resolves, the gate is silent
    # (guards against the negative test regressing to "always raise").
    monkeypatch.setattr(browser.importlib.util, "find_spec", lambda _name: object())
    require_playwright_if_needed(_write_sites(tmp_path, flagged=True))
