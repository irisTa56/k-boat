"""Feed parsing — ``feedparser`` over raw bytes (CON-002).

``Entry`` is the shared ingest shape across the feed and scrape paths. ``title``,
``summary``, and ``published_at`` are populated for feed entries (any may still
be ``None`` for a sparse item) but are documented **feed-only**: the scrape path
(``scrape.scrape_index``) leaves them ``None`` and the subagent's page fetch
supplies a title for any keep (REQ-005). ``canonical_url`` is the dedupe key
(PAT-002), so it is typed ``CanonicalUrl`` and always set.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from typing import Literal

import feedparser

from feed_filter.canonical import CanonicalUrl, canonical_url, resolve_link

EntryKind = Literal["feed", "scrape"]


@dataclass(frozen=True)
class Entry:
    """One ingestable item. ``kind`` records which path produced it."""

    canonical_url: CanonicalUrl
    title: str | None
    summary: str | None
    published_at: int | None
    kind: EntryKind


def _published_at(entry: object) -> int | None:
    """UTC unix timestamp from a feedparser entry, or ``None``.

    feedparser exposes parsed dates as UTC ``time.struct_time``. ``time.mktime``
    would read them as local time and shift by the host offset, so use
    ``calendar.timegm`` to keep the UTC contract.
    """
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            try:
                return int(calendar.timegm(value))
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def parse_feed(body: bytes, base_url: str) -> list[Entry]:
    """Parse an RSS/Atom body into ``Entry`` objects, newest first.

    ``body`` is raw bytes (CON-002), never ``str``: ``feedparser.parse`` treats a
    ``str`` that looks like a URL or path as a fetch/file-open instruction, so a
    ``str`` overload here would turn this pure parser into an SSRF/file-read
    footgun. Callers pass ``FetchResult.content``.

    Entries whose link is unresolvable (missing / fragment-only / non-http) are
    skipped (``resolve_link``). A malformed body that ``feedparser`` cannot
    recover any entry from yields ``[]`` rather than raising — a dead feed is an
    empty observation, not a crash (REQ-007/REQ-008 handle it downstream).

    Ordering: dated entries by ``published_at`` descending, undated entries in
    source order at the tail. The per-site cap downstream then keeps the newest
    (CON-005), and the discover preview shows the same order a run would ingest.
    """
    parsed = feedparser.parse(body)
    raw_entries = getattr(parsed, "entries", []) or []
    # ``bozo`` with zero recovered entries = unparseable body. ``bozo`` *with*
    # entries (feedparser repaired partial damage) falls through normally.
    if getattr(parsed, "bozo", False) and not raw_entries:
        return []

    out: list[Entry] = []
    for entry in raw_entries:
        absolute = resolve_link(base_url, getattr(entry, "link", None))
        if absolute is None:
            continue
        out.append(
            Entry(
                canonical_url=canonical_url(absolute),
                title=getattr(entry, "title", None) or None,
                summary=getattr(entry, "summary", None) or None,
                published_at=_published_at(entry),
                kind="feed",
            )
        )

    # Two-pass split (rather than an ``or 0`` sort key) so an entry dated at the
    # epoch is not sorted alongside the genuinely undated tail.
    dated = [e for e in out if e.published_at is not None]
    undated = [e for e in out if e.published_at is None]
    dated.sort(key=lambda e: e.published_at if e.published_at is not None else 0, reverse=True)
    return dated + undated
