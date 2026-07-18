"""Raw HTTP fetch — the single network boundary.

Synchronous throughout: feed-filter processes a handful of sites in order, so a
sync ``httpx.Client`` is used everywhere; there is no ``asyncio`` boundary.

``fetch`` returns the response as both raw ``bytes`` and decoded ``text``. Feeds
MUST be parsed from the raw bytes (``feedparser`` does its own encoding
detection from the XML declaration, which httpx's text decoding would pre-empt);
HTML scraping consumes ``text``.

Any HTTP-status (``>= 400``) or transport failure raises ``FetchError``. Callers
consume it (a gather-time failure leaves the entry unseen so the
next run retries it naturally).

Before raising on a throttling status (``429``/``503``), ``fetch`` retries a
bounded number of times, honoring the server's ``Retry-After`` header. The
forum gather loop polls ``/t/<id>.json`` once per due topic in a tight sequence
and trips Discourse's anonymous rate limit, so this client-side
back-off keeps a run complete instead of shedding throttled topics to the next
run. httpx's own transport ``retries`` covers only connection failures, not
status codes, so the status-based retry lives here at the single fetch boundary.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

# Generous timeout: a slow feed is worth waiting for in a batch routine that
# runs infrequently. Applied via the client built by ``build_client``.
DEFAULT_TIMEOUT = httpx.Timeout(20.0)

# Descriptive User-Agent so site operators can identify the crawler. Sent on
# every request regardless of which client the caller injects.
USER_AGENT = "feed-filter (+https://github.com/irisTa56/feed-filter)"

# Statuses worth retrying: the server is telling us to slow down or come back,
# not that the resource is wrong. 429 = Too Many Requests (Discourse rate
# limit), 503 = Service Unavailable; both commonly carry a ``Retry-After``.
RETRY_STATUSES = frozenset({429, 503})

# Default number of *additional* attempts after the first on a retryable status
# (so up to ``1 + DEFAULT_MAX_RETRIES`` GETs). Bounded so a persistently
# throttled site fails fast rather than stalling the batch.
DEFAULT_MAX_RETRIES = 3

# Cap on a single back-off wait. Honors ``Retry-After`` up to this ceiling so a
# hostile or mis-set header (``Retry-After: 86400``) cannot hang a run.
MAX_RETRY_WAIT = 30.0

# The default back-off sleep, held as a module attribute rather than a parameter
# default so tests can neuter it without a real delay. Gather paths call ``fetch``
# without threading a ``sleep``, so this is the only back-off seam they hit; a
# test patches ``feed_filter.fetch._sleep`` to a no-op, which is scoped to fetch
# and leaves the global ``time.sleep`` untouched for every other caller.
_sleep = time.sleep


def _retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying a throttled response.

    Prefers the server's ``Retry-After`` header in its integer-seconds form
    (what Discourse sends); when the header is absent or not an integer, falls
    back to exponential back-off (1s, 2s, 4s, … by ``attempt``). The HTTP-date
    form of ``Retry-After`` is intentionally not parsed — it would pull in a
    wall-clock read that breaks deterministic testing, and the throttling
    servers we hit use integer seconds. Clamped to ``MAX_RETRY_WAIT``.
    """
    header = resp.headers.get("retry-after", "").strip()
    try:
        wait = float(int(header))
    except ValueError:
        wait = float(1 << attempt)  # 1, 2, 4, ... by attempt index
    return max(0.0, min(wait, MAX_RETRY_WAIT))


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


def fetch(
    url: str,
    *,
    client: httpx.Client,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], None] | None = None,
) -> FetchResult:
    """GET ``url`` and return its body, or raise ``FetchError``.

    Following redirects is a correctness requirement of this fetch (we always
    want the final resource), so it is enforced per-request here regardless of
    the injected client's own policy — that is why a bare ``MockTransport``
    client still follows redirects in tests. Connection identity (timeout,
    User-Agent) comes from the client (``build_client``).

    On a throttling status (``RETRY_STATUSES``) the GET is retried up to
    ``max_retries`` times, waiting ``_retry_after_seconds`` between attempts
    (honoring ``Retry-After``); once the retries are spent the final throttled
    response raises ``FetchError`` like any other status. Non-retryable statuses
    and transport failures raise on the first attempt, unchanged. ``sleep``
    defaults (when ``None``) to the module-level ``_sleep`` seam, resolved here
    at call time rather than bound as a parameter default (see ``_sleep`` for
    why). Callers that pass ``sleep`` explicitly (see ``test_fetch``) exercise
    the back-off timing directly.
    """
    if sleep is None:
        sleep = _sleep
    # Clamp so the loop always runs at least once (a negative max_retries means
    # "no retries", not "no attempt"); this also makes the post-loop line below
    # genuinely unreachable for every input.
    retries = max(0, max_retries)
    for attempt in range(retries + 1):
        try:
            resp = client.get(url, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # Retry a throttling status while attempts remain; the final
            # attempt (attempt == retries) falls through to raise.
            if status in RETRY_STATUSES and attempt < retries:
                sleep(_retry_after_seconds(exc.response, attempt))
                continue
            raise FetchError(url, status=status) from exc
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
    # The final loop iteration always returns or raises, so this is unreachable;
    # assert the invariant rather than fabricate a result the caller might catch.
    raise AssertionError("fetch retry loop exited without returning or raising")
