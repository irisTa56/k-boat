"""Per-site new-entry aggregation (REQ-003, REQ-006, REQ-008).

``gather_new`` is the read-only half of a run: fetch a site's current entries,
count how many the live page yields *before* dedupe (``index_matches``), then
filter out already-seen URLs and clamp to the per-site cap. It records nothing —
the seen-store is written only by the ``remind`` / ``mark-seen`` CLI paths, after
a reminder is created (REQ-009). A fetch failure is captured into ``error`` and
leaves the entry list empty so nothing is recorded and the next run retries it
naturally (REQ-008).

``index_matches`` separates two outcomes that look identical after filtering: a
scrape site whose stored pattern no longer matches the live page (``index_matches
== 0`` → ``zero_links``, self-heal fires, REQ-006) versus a quiet-but-healthy day
(``index_matches > 0`` but every match already seen → no heal).
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
    fetch failure message, if any — when set, ``entries`` is empty.
    """

    entries: list[Entry]
    index_matches: int
    zero_links: bool
    error: str | None


def fetch_entries(site: SiteConfig, *, client: httpx.Client) -> list[Entry]:
    """All of ``site``'s current entries, uncapped and unfiltered.

    Branches on kind, then on transport. The feed path parses raw bytes (CON-002);
    the scrape path resolves links against a base URL and matches
    ``article_url_pattern``. A ``requires_browser`` site routes its gather fetch
    through the Playwright path (``browser.fetch_raw`` / ``browser.fetch_html``)
    while every other site keeps the ``httpx`` path; both transports feed the same
    parsers, so they yield identical ``Entry`` lists (REQ-002). ``client`` is unused
    on the browser branch (F2). Raises ``FetchError`` (httpx) or ``BrowserFetchError``
    (browser) on a fetch failure.

    Used both by ``gather_new`` (then filtered) and by registration's cold-start
    snapshot (REQ-002), which needs the full back-catalog, not the filtered slice.
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


def gather_new(conn: sqlite3.Connection, site: SiteConfig, *, client: httpx.Client) -> GatherResult:
    """Fetch ``site``, drop already-seen entries, clamp to the per-site cap.

    A ``FetchError`` is absorbed into ``error`` with an empty entry list
    (REQ-008); nothing is recorded seen here regardless.
    """
    try:
        all_entries = fetch_entries(site, client=client)
    except (FetchError, BrowserFetchError) as exc:
        # Both transports' fetch failures absorb into the per-site error with an
        # empty entry list (REQ-008); nothing is recorded seen, so the next run
        # retries naturally.
        return GatherResult(entries=[], index_matches=0, zero_links=False, error=str(exc))

    index_matches = len(all_entries)
    fresh = [e for e in all_entries if not is_seen(conn, e.canonical_url)]
    entries = fresh[:DEFAULT_PER_SITE_CAP]
    zero_links = site.kind == "scrape" and index_matches == 0
    return GatherResult(
        entries=entries,
        index_matches=index_matches,
        zero_links=zero_links,
        error=None,
    )
