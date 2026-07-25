"""Per-site new-entry aggregation.

``gather_new`` is the read-only half of a run: fetch a site's current entries,
count how many the live page yields *before* dedupe (``index_matches``), then
filter out already-seen URLs and clamp to the per-site cap. It records nothing —
the seen-store is written only by the ``remind`` / ``mark-seen`` CLI paths, after
a note is written. A fetch failure is captured into ``error`` and
leaves the entry list empty so nothing is recorded and the next run retries it
naturally.

``index_matches`` separates two outcomes that look identical after filtering: a
scrape site whose stored pattern no longer matches the live page (``index_matches
== 0`` → ``zero_links``, self-heal fires) versus a quiet-but-healthy day
(``index_matches > 0`` but every match already seen → no heal).

``gather_new`` is the sequential composition of two halves that ``cmd_new_entries``
also drives separately so it can fetch hosts concurrently:

- ``fetch_site`` — the **network-only** half (``fetch_entries`` with the fetch
  failure absorbed into ``FetchOutcome.error``). It touches no DB, so it is safe
  to run in a worker thread.
- ``filter_gathered`` — the **DB-touching** half (``is_seen`` filter + per-site
  cap + the ``zero_links`` signal). It reads the seen-store, so it stays on the
  main thread.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import httpx

from feed_filter import browser
from feed_filter.browser import BrowserFetchError
from feed_filter.config import DEFAULT_PER_SITE_CAP
from feed_filter.feeds import Entry, parse_feed
from feed_filter.fetch import FetchError, fetch
from feed_filter.scrape import scrape_index
from feed_filter.seen import is_seen
from feed_filter.sites import SiteConfig


@dataclass(frozen=True)
class GatherResult:
    """Outcome of gathering one site.

    ``entries`` are the new (unseen), capped items to judge. ``index_matches`` is
    the pre-filter count of feed entries / pattern matches. ``zero_links`` is the
    self-heal signal (scrape site, live page matched nothing). ``error`` is the
    fetch failure message, if any — when set, ``entries`` is empty, and
    ``unexpected`` says whether it came from an exception the gather did not
    classify (carried from ``FetchOutcome``).
    """

    entries: list[Entry]
    index_matches: int
    zero_links: bool
    error: str | None
    unexpected: bool = False


@dataclass(frozen=True)
class FetchOutcome:
    """The network-only half of a gather: a site's raw entries, or a fetch failure.

    ``entries`` are the site's current items, uncapped and unfiltered (as
    ``fetch_entries`` returns them); ``error`` is the fetch failure message, if
    any — when set, ``entries`` is empty. ``unexpected`` marks an error the gather
    could **not** classify — that and no more: it says the failure did not arrive as
    a ``FetchError``/``BrowserFetchError``, not whose fault it is. ``fetch_site``
    only ever produces the classified kind, so the flag is set by whoever absorbs
    the unclassified one (``cli._fetch_all``), and it is a typed signal rather than
    a prefix the reader has to parse back out of ``error``. Carries no DB-derived
    state,
    so it can be produced off the main thread and handed to ``filter_gathered``
    later (``cmd_new_entries`` fetches hosts concurrently, then filters serially).
    """

    entries: list[Entry]
    error: str | None
    unexpected: bool = False


def fetch_entries(site: SiteConfig, *, client: httpx.Client) -> list[Entry]:
    """All of ``site``'s current entries, uncapped and unfiltered.

    Branches on kind, then on transport. The feed path parses raw bytes;
    the scrape path resolves links against a base URL and matches
    ``article_url_pattern``. A ``requires_browser`` site routes its gather fetch
    through the Playwright path (``browser.fetch_raw`` / ``browser.fetch_html``)
    while every other site keeps the ``httpx`` path; both transports feed the same
    parsers, so they yield identical ``Entry`` lists. ``client`` is unused
    on the browser branch (F2). Raises ``FetchError`` (httpx) or ``BrowserFetchError``
    (browser) on a fetch failure.

    Used both by ``gather_new`` (then filtered) and by registration's cold-start
    snapshot, which needs the full back-catalog, not the filtered slice.
    """
    if site.feed_url is not None:
        if site.requires_browser:
            # Anchor parsing to the post-redirect URL (element 0), matching the
            # httpx path's ``final_url`` so relative entry links resolve identically
            # (F5 — deliberately unlike loose-feeds, which anchors to ``feed_url``).
            base_url, body = browser.fetch_raw(site.feed_url)
        else:
            result = fetch(site.feed_url, client=client)
            base_url, body = result.final_url, result.content
        return parse_feed(body, base_url)
    # SiteConfig's exactly-one invariant guarantees a scrape site has both fields.
    assert site.index_url is not None and site.article_url_pattern is not None
    if site.requires_browser:
        # Both transports anchor link resolution to the post-redirect URL, so a
        # redirecting index (http->https, www host) resolves entry links identically.
        base_url, html = browser.fetch_html(site.index_url)
    else:
        result = fetch(site.index_url, client=client)
        base_url, html = result.final_url, result.text
    return scrape_index(html, base_url, site.article_url_pattern)


def fetch_site(site: SiteConfig, *, client: httpx.Client) -> FetchOutcome:
    """Fetch ``site``'s current entries, absorbing a fetch failure into ``error``.

    The network-only half of ``gather_new``: it touches no DB, so it is safe to
    run in a worker thread (``cmd_new_entries`` fetches independent hosts
    concurrently). A ``FetchError``/``BrowserFetchError`` becomes an ``error`` with
    an empty entry list, exactly as ``gather_new`` absorbed it inline;
    nothing is recorded seen, so the next run retries naturally.

    A ``requires_browser`` site still routes through Playwright, whose sync API is
    not thread-safe — ``cmd_new_entries`` keeps those fetches on the main thread.
    """
    try:
        entries = fetch_entries(site, client=client)
    except (FetchError, BrowserFetchError) as exc:
        return FetchOutcome(entries=[], error=str(exc))
    return FetchOutcome(entries=entries, error=None)


def filter_gathered(
    conn: sqlite3.Connection, site: SiteConfig, outcome: FetchOutcome
) -> GatherResult:
    """Turn a completed ``FetchOutcome`` into a ``GatherResult``.

    The DB-touching half of ``gather_new``: drop already-seen entries (``is_seen``),
    clamp to the per-site cap, and derive the ``zero_links`` self-heal signal. It
    reads the seen-store, so it runs on the main thread only. A fetch failure
    passes straight through as an empty, error-bearing result.
    """
    if outcome.error is not None:
        return GatherResult(
            entries=[],
            index_matches=0,
            zero_links=False,
            error=outcome.error,
            unexpected=outcome.unexpected,
        )

    index_matches = len(outcome.entries)
    fresh = [e for e in outcome.entries if not is_seen(conn, e.canonical_url)]
    entries = fresh[:DEFAULT_PER_SITE_CAP]
    zero_links = site.kind == "scrape" and index_matches == 0
    return GatherResult(
        entries=entries,
        index_matches=index_matches,
        zero_links=zero_links,
        error=None,
    )


def gather_new(conn: sqlite3.Connection, site: SiteConfig, *, client: httpx.Client) -> GatherResult:
    """Fetch ``site``, drop already-seen entries, clamp to the per-site cap.

    The sequential composition of ``fetch_site`` then ``filter_gathered``; a
    ``FetchError`` is absorbed into ``error`` with an empty entry list,
    nothing is recorded seen regardless. ``cmd_new_entries`` calls the two halves
    separately so it can fetch hosts concurrently while keeping the seen-filter on
    the main thread; this composed form is retained as the tested single-site
    contract. Registration's snapshot paths (``cmd_add_site`` / ``cmd_heal_site``)
    do not use it — they call ``fetch_entries`` directly for the full uncapped
    back-catalog, without the seen-filter or per-site cap ``gather_new`` applies.
    """
    return filter_gathered(conn, site, fetch_site(site, client=client))
