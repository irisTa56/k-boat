"""Scrape ingestion — article links off an index page (ported, reduced).

For non-feed sites, ``scrape_index`` walks every ``<a>`` on the index HTML,
resolves same-host absolute URLs, drops query/fragment, and keeps those whose
path matches ``article_url_pattern``. Entries carry ``kind="scrape"`` with no
metadata (``title``/``summary``/``published_at`` are ``None``); the subagent's
page fetch supplies a title for any keep (REQ-005).

Ported from loose-feeds ``ingest/scrape.py``, stripped of SSRF guards, age
filtering, and the metadata store — ``sites.toml`` is trusted (SEC-001) and the
seen-store lives elsewhere.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from feed_filter.canonical import CanonicalUrl, canonical_url, resolve_link
from feed_filter.feeds import Entry


def scrape_index(
    html: str,
    index_url: str,
    pattern: str,
    *,
    max_entries: int | None = None,
) -> list[Entry]:
    """Extract article ``Entry`` objects from ``html`` in DOM order.

    Links are resolved against ``index_url`` and kept only if same-host and
    path-matching ``pattern``. ``max_entries`` caps enumeration in DOM order (a
    paginated archive would otherwise grow unbounded); ``None`` means no cap. An
    invalid ``pattern`` raises ``re.error`` — a config bug surfaces, not silently
    empty.

    Both the ``pattern`` match and the dedupe key are computed on the
    **canonical** URL (PAT-002), not the raw path. ``discover`` synthesizes the
    pattern from the canonical cluster key, so matching here on the canonical path
    keeps the two sides symmetric: a ``//`` or trailing-slash variant of one
    article (``/post`` vs ``/post/`` vs ``/blog//post``) collapses to the single
    key the seen-store dedupes against and matches identically to the form
    discovery counted — otherwise the registration preview's ``entry_count`` and
    what this ingester emits could diverge. Query and fragment are dropped first
    so rotating tracking params on an index link don't fork an article.
    """
    rx = re.compile(pattern)
    tree = HTMLParser(html)
    seen: set[CanonicalUrl] = set()
    out: list[Entry] = []
    for link in tree.css("a"):
        absolute = resolve_link(index_url, link.attributes.get("href"), require_same_host=True)
        if absolute is None:
            continue
        parts = urlsplit(absolute)
        cu = canonical_url(f"{parts.scheme}://{parts.netloc}{parts.path}")
        if not rx.search(urlsplit(cu).path or "/"):
            continue
        if cu in seen:
            continue
        seen.add(cu)
        out.append(
            Entry(
                canonical_url=cu,
                title=None,
                summary=None,
                published_at=None,
                kind="scrape",
            )
        )
        if max_entries is not None and len(out) >= max_entries:
            break
    return out
