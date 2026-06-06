"""Opt-in Playwright browser fetch path for JS-rendered / anti-bot sites.

A synchronous, SSRF-free port of loose-feeds' ``ingest/browser.py`` (CON-003).
Only the **gather** primitives are here: ``fetch_html`` (rendered index HTML for
the scrape path) and ``fetch_raw`` (raw feed XML bytes for the feed path). What
loose-feeds carried that this drops, and why:

- async (``playwright.async_api``) → ``sync_api``: feed-filter is single-threaded
  throughout (CON-001), so there is no event loop and the lazy singleton needs no
  lock — a plain module-global guard suffices (the loose-feeds "double-checked"
  init existed only to serialize concurrent ``asyncio`` first-callers).
- the two-layer SSRF guard (host allowlist + ``context.route`` subresource guard
  + ``AllowlistOverride``): ``sites.toml`` is trusted single-user config (SEC-001),
  so there is no untrusted URL to defend against. The body-size cap is **retained**
  as the sole body backstop now that the route-guard is gone.
- per-article ``fetch_and_extract_via_browser``: out of scope for the gather path
  (CON-004); it lands only if a real site needs a gated body the judge cannot read.

The Playwright dependency is an optional extra (CON-002): the import is lazy, so a
base install that flags no ``requires_browser`` site never imports it. The startup
gate ``require_playwright_if_needed`` fails fast with the install command if a
flagged site is registered but the extra is missing (REQ-006).
"""

from __future__ import annotations

import contextlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from feed_filter.config import browser_state_path
from feed_filter.sites import SiteConfig, load_sites

if TYPE_CHECKING:
    # Real Playwright types, imported only for annotations so the runtime path
    # stays opt-in. ``ty`` resolves these via the dev-only ``playwright`` package;
    # the base/runtime install pulls them in only through the ``browser`` extra.
    from playwright.sync_api import Browser, BrowserContext, Playwright

# Generous per-fetch timeout (seconds), matching the httpx path's 20 s: a slow
# render is worth waiting for in an infrequent batch routine. ``page.goto`` takes
# milliseconds, so callers' value is multiplied by 1000 at the call site.
DEFAULT_BROWSER_TIMEOUT_SEC = 20.0

# Per-fetch body cap, applied to both rendered HTML and raw feed bytes. This is
# the SOLE body backstop now that loose-feeds' SSRF route-guard is dropped
# (SEC-001) — do NOT remove it to "match httpx". Without it a runaway page (a
# CDN download URL pasted in, or a malicious multi-hundred-MB body) is fully
# buffered into the process before any parse. 10 MiB covers every real page
# observed; rendered long-form article HTML legitimately exceeds 2 MiB.
_MAX_BROWSER_BODY_BYTES = 10 * 1024 * 1024


class MissingPlaywrightError(RuntimeError):
    """Raised when a ``requires_browser`` site is registered but Playwright is absent.

    Failing fast at the startup gate (rather than at the first fetch) means the
    operator sees the exact install command instead of a raw ``ModuleNotFoundError``
    deep in the gather path (REQ-006).
    """


# Shared by both gates (registered-sites and about-to-be-registered) so the
# operator sees one install command regardless of which path catches the miss.
_INSTALL_HINT = (
    "the 'playwright' package is not installed. Install it with:\n"
    "  uv sync --extra browser && uv run playwright install chromium"
)


class BrowserFetchError(RuntimeError):
    """A browser-path fetch failure (navigation, timeout, HTTP >= 400, oversize body).

    The type is distinct from ``FetchError`` so the gather dispatch can absorb both
    into a per-site ``error`` without inspecting the message (REQ-008).
    """


@dataclass
class BrowserBundle:
    """The live Playwright instance, Chromium browser, and persistent context.

    A single instance per CLI process (REQ-003), owned by the module-global
    singleton below and torn down by ``close_browser``. The single context is what
    lets clearance cookies persist to one ``storage_state`` file across runs
    (REQ-004); ``storage_state_path`` is captured here so ``close_browser`` writes
    back to the same file ``get_browser`` loaded from.
    """

    playwright: Playwright
    browser: Browser
    context: BrowserContext
    storage_state_path: Path


# Lazy singleton (REQ-003). No lock: the CLI is single-threaded (CON-001), so the
# only way two bundles could exist is re-entrant calls within one thread, which
# the ``is not None`` guard already prevents.
_bundle: BrowserBundle | None = None


def _playwright_installed() -> bool:
    """True iff ``import playwright`` would resolve, without running its module body.

    ``find_spec`` is cheaper and side-effect-free compared to a try/except import,
    and is what the startup gate consults to decide whether to fail fast (REQ-006).
    """
    return importlib.util.find_spec("playwright") is not None


def _desktop_user_agent(version: str) -> str:
    """Build a non-headless desktop User-Agent from the live Chromium version (REQ-005).

    Playwright's default UA carries ``HeadlessChrome/<ver>``, the exact marker
    Cloudflare's first-line bot check pattern-matches to serve a challenge headless
    Chromium cannot solve (no clearance cookie is ever issued; the fetch stalls at
    the load timeout). Rebuilding the UA from ``browser.version`` — rather than
    string-replacing — drops the ``Headless`` marker and stays in sync with whatever
    Chromium Playwright bundles, so a version bump cannot drift back toward
    re-detection. The macOS platform string is fixed: the routine runs locally on
    the user's Mac (CON-001). This is the entire Cloudflare mechanism — a site that
    still serves an interactive challenge after the strip is unsupported (GUD-003),
    surfaced as a per-site error rather than worked around.
    """
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Safari/537.36"
    )


def get_browser() -> BrowserBundle:
    """Return the process-wide browser bundle, launching Chromium on first call.

    Lazy (REQ-003): the ``playwright`` import and Chromium launch happen only here,
    so a run with no ``requires_browser`` site pays nothing. The persisted
    ``storage_state`` is loaded if the file exists; on a clean machine it is absent,
    so ``storage_state=None`` is passed — Playwright raises on a missing path, and
    the file only appears after the first ``close_browser`` writes cookies (F4).
    """
    global _bundle
    if _bundle is not None:
        return _bundle

    # Lazy, opt-in import: kept inside the function so the base install and the
    # httpx path never import Playwright (CON-002). Reached only after the startup
    # gate / add-site check has confirmed the extra is installed (REQ-006).
    from playwright.sync_api import sync_playwright

    state_path = browser_state_path()
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch()
    context = browser.new_context(
        user_agent=_desktop_user_agent(browser.version),
        storage_state=str(state_path) if state_path.exists() else None,
    )
    _bundle = BrowserBundle(
        playwright=playwright,
        browser=browser,
        context=context,
        storage_state_path=state_path,
    )
    return _bundle


def close_browser() -> None:
    """Persist cookies and tear down the browser; a no-op if none was launched (REQ-003).

    Idempotent: the slot is cleared first, so a re-entrant or double call (e.g. a
    ``finally`` plus a shutdown hook) short-circuits. Each step is best-effort —
    this runs from a ``try/finally`` teardown where raising would mask the command's
    real outcome, and the common real failure (the Playwright driver dying on the
    same signal we got) leaves the resources already released. A failed cookie write
    only costs the next run a re-challenge, never the run in progress.
    """
    global _bundle
    bundle = _bundle
    if bundle is None:
        return
    _bundle = None
    with contextlib.suppress(Exception):
        bundle.context.storage_state(path=str(bundle.storage_state_path))
    with contextlib.suppress(Exception):
        bundle.browser.close()
    with contextlib.suppress(Exception):
        bundle.playwright.stop()


def require_playwright_if_needed(sites_toml_path: Path) -> None:
    """Fail fast if any registered site needs the browser but the extra is missing.

    Inspects the on-disk registry. There is no explicit ``exists()`` guard — that
    is delegated to ``load_sites``, which returns ``[]`` for a missing file, so a
    fresh clone is a no-op. The param is a plain ``Path`` (not ``Path | None``):
    every caller passes ``config.sites_path()`` so the gate sees the same registry
    — honoring ``FEED_FILTER_SITES`` — as everything else (F8). Raises
    ``MissingPlaywrightError`` with the exact install command rather than letting a
    raw ``ModuleNotFoundError`` surface mid-gather (REQ-006).
    """
    flagged = [s.id for s in load_sites(sites_toml_path) if s.requires_browser]
    if not flagged:
        return
    if _playwright_installed():
        return
    raise MissingPlaywrightError(
        f"sites.toml registers requires_browser=true site(s) {flagged!r} but {_INSTALL_HINT}"
    )


def require_playwright_for(site: SiteConfig) -> None:
    """Fail fast if ``site`` needs the browser but Playwright is absent (add-site, REQ-006).

    The on-disk gate ``require_playwright_if_needed`` cannot see a site mid-
    registration — config is written last (REQ-002) — so ``add-site`` checks the
    in-memory ``SiteConfig`` directly, before its cold-start snapshot, so a
    Playwright-less machine gets this friendly error instead of a raw
    ``ModuleNotFoundError`` from the browser snapshot fetch.
    """
    if site.requires_browser and not _playwright_installed():
        raise MissingPlaywrightError(
            f"site {site.id!r} sets requires_browser=true but {_INSTALL_HINT}"
        )


def fetch_html(url: str, *, timeout: float = DEFAULT_BROWSER_TIMEOUT_SEC) -> tuple[str, str]:
    """Return ``(post-redirect URL, rendered HTML)`` for ``url`` (scrape index, REQ-002).

    The post-redirect URL (``response.url``) is the base for resolving relative
    article links, matching the httpx path's ``final_url`` and ``fetch_raw``'s
    element 0 — so all four gather paths anchor link resolution to the same
    post-redirect base and yield identical ``Entry`` lists across transports
    regardless of an index redirect (http->https, ``www`` host, etc.). When ``goto``
    returns no response (same-document SPA nav), the requested ``url`` is the base.

    Raises ``BrowserFetchError`` on a navigation/timeout failure, an HTTP >= 400
    response, or a rendered body over the size cap. The caller (``pipeline``) feeds
    the HTML to ``scrape_index`` exactly as it would the httpx ``text``.
    """
    bundle = get_browser()
    page = bundle.context.new_page()
    try:
        try:
            response = page.goto(url, wait_until="load", timeout=timeout * 1000.0)
        except Exception as exc:
            raise BrowserFetchError(f"navigation failed for {url!r}: {exc}") from exc
        # ``goto`` returns None for a same-document navigation (SPA history push /
        # anchor scroll); unlike fetch_raw we tolerate it and still return the
        # rendered DOM, since the index HTML is what we are after. Only a real
        # response with a >= 400 status is a failure.
        if response is not None and response.status >= 400:
            raise BrowserFetchError(f"fetch of {url!r} returned HTTP {response.status}")
        try:
            html = page.content()
        except Exception as exc:
            raise BrowserFetchError(f"content() failed for {url!r}: {exc}") from exc
        # ``len(html)`` is the code-point count, not the UTF-8 byte length the
        # constant names; for mostly-ASCII HTML they are equal and for CJK-heavy
        # pages the code-point count is the smaller, so the cap fails safe.
        if len(html) > _MAX_BROWSER_BODY_BYTES:
            raise BrowserFetchError(
                f"rendered HTML for {url!r} exceeds {_MAX_BROWSER_BODY_BYTES} bytes "
                f"({len(html)} chars)"
            )
        base_url = str(response.url) if response is not None else url
        return base_url, html
    finally:
        page.close()


def fetch_raw(url: str, *, timeout: float = DEFAULT_BROWSER_TIMEOUT_SEC) -> tuple[str, bytes]:
    """Return ``(post-redirect URL, raw response bytes)`` for a feed (the feed path, REQ-002).

    Feeds are XML: ``page.content()`` would wrap them in the browser's DOM tree-view
    envelope, which ``feedparser`` cannot parse, so the on-the-wire ``response.body()``
    is returned instead. The post-redirect ``response.url`` is handed back as the base
    for resolving relative entry links, matching the httpx path's ``final_url`` so the
    two transports yield identical ``Entry`` lists (REQ-002). Raises
    ``BrowserFetchError`` on navigation/timeout, a missing response, HTTP >= 400, or
    an oversize body.

    Risk (RISK-003): the test fake pre-stuffs ``response.body()``, so it cannot
    detect a future Playwright version that stops surfacing raw bytes for a
    navigation Chromium rendered as an XML tree-view (it would start returning
    empty bytes here). Only a real-Chromium integration test would catch that, and
    such tests are deferred — until then this is an accepted, documented blind spot.
    """
    bundle = get_browser()
    page = bundle.context.new_page()
    try:
        try:
            response = page.goto(url, wait_until="load", timeout=timeout * 1000.0)
        except Exception as exc:
            raise BrowserFetchError(f"navigation failed for {url!r}: {exc}") from exc
        if response is None:
            # Same-document navigations (anchor scroll, history.pushState) return
            # None — not expected for a top-level feed URL; fail rather than emit
            # empty bytes that would later parse as a bozo (empty) feed.
            raise BrowserFetchError(f"no navigation response for {url!r}")
        if response.status >= 400:
            raise BrowserFetchError(f"raw fetch of {url!r} returned HTTP {response.status}")
        try:
            body = response.body()
        except Exception as exc:
            raise BrowserFetchError(f"body() failed for {url!r}: {exc}") from exc
        if len(body) > _MAX_BROWSER_BODY_BYTES:
            raise BrowserFetchError(
                f"raw body for {url!r} exceeds {_MAX_BROWSER_BODY_BYTES} bytes ({len(body)} bytes)"
            )
        return str(response.url), body
    finally:
        page.close()
