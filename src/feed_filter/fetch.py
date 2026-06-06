"""Raw HTTP fetch — the single network boundary (GUD-005).

Synchronous throughout: feed-filter processes a handful of sites in order, so a
sync ``httpx.Client`` is used everywhere; there is no ``asyncio`` boundary.

``fetch`` returns the response as both raw ``bytes`` and decoded ``text``. Feeds
MUST be parsed from the raw bytes (CON-002: ``feedparser`` does its own encoding
detection from the XML declaration, which httpx's text decoding would pre-empt);
HTML scraping consumes ``text``.

Any HTTP-status (``>= 400``) or transport failure raises ``FetchError``. Callers
consume it per REQ-008 (a gather-time failure leaves the entry unseen so the
next run retries it naturally).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

# Generous timeout: a slow feed is worth waiting for in a batch routine that
# runs infrequently. Applied via the client built by ``build_client``.
DEFAULT_TIMEOUT = httpx.Timeout(20.0)

# Descriptive User-Agent so site operators can identify the crawler. Sent on
# every request regardless of which client the caller injects.
USER_AGENT = "feed-filter (+https://github.com/irisTa56/feed-filter)"


class FetchError(Exception):
    """An HTTP-status or transport failure for a single fetch.

    ``status`` is the HTTP status code for a ``>= 400`` response, or ``None``
    for a transport-level failure (timeout, connection refused, DNS).
    """

    def __init__(self, url: str, *, status: int | None = None) -> None:
        self.url = url
        self.status = status
        detail = f"HTTP {status}" if status is not None else "transport error"
        super().__init__(f"fetch failed for {url}: {detail}")


@dataclass(frozen=True)
class FetchResult:
    """A successful (``< 400``, post-redirect) fetch.

    ``final_url`` is the URL after following redirects, used as the base for
    resolving relative links during parsing.
    """

    content: bytes
    text: str
    content_type: str
    final_url: str
    status: int


def build_client() -> httpx.Client:
    """Construct the configured sync client (timeout + User-Agent + redirects).

    Owns connection-identity config (timeout + User-Agent). Redirect-following
    is owned by ``fetch`` per request, not set here, so there is one source of
    truth for it. The CLI builds one client per run and threads it through
    ``fetch``; tests inject their own ``httpx.Client`` over a ``MockTransport``.
    """
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )


def fetch(url: str, *, client: httpx.Client) -> FetchResult:
    """GET ``url`` and return its body, or raise ``FetchError``.

    Following redirects is a correctness requirement of this fetch (we always
    want the final resource), so it is enforced per-request here regardless of
    the injected client's own policy — that is why a bare ``MockTransport``
    client still follows redirects in tests. Connection identity (timeout,
    User-Agent) comes from the client (``build_client``).
    """
    try:
        resp = client.get(url, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise FetchError(url, status=exc.response.status_code) from exc
    except httpx.HTTPError as exc:
        # Covers timeouts, connection errors, DNS failures — every non-status
        # httpx failure is a transport error with no meaningful status code.
        raise FetchError(url) from exc
    return FetchResult(
        content=resp.content,
        text=resp.text,
        content_type=resp.headers.get("content-type", ""),
        final_url=str(resp.url),
        status=resp.status_code,
    )
