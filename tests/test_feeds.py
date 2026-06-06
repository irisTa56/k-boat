"""Behavior tests for feed parsing (TASK-018)."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from feed_filter.feeds import _published_at, parse_feed

FIXTURES = Path(__file__).parent / "fixtures"


def test_rss_canonicalizes_and_orders_newest_first() -> None:
    body = (FIXTURES / "rss.xml").read_bytes()
    entries = parse_feed(body, "https://example.com/")

    # Second post (Jun 03) is newer than first (Jun 02), so it leads.
    assert [e.canonical_url for e in entries] == [
        "https://example.com/posts/second",
        "https://example.com/posts/first",
    ]
    first = entries[1]
    assert first.title == "First Post"
    assert first.summary == "Summary of first post."
    assert first.kind == "feed"
    assert all(e.published_at is not None for e in entries)


def test_atom_resolves_relative_links() -> None:
    body = (FIXTURES / "atom.xml").read_bytes()
    entries = parse_feed(body, "https://atom.example.com/")

    assert len(entries) == 1
    entry = entries[0]
    assert entry.canonical_url == "https://atom.example.com/articles/one"
    assert entry.title == "Atom Entry One"
    assert entry.summary == "First atom entry."


def test_malformed_feed_returns_empty() -> None:
    assert parse_feed(b"<<< this is not a feed", "https://example.com/") == []


# A feed mixing a dated entry, an undated entry, and an unresolvable
# (fragment-only) link: the undated entry sorts to the tail and the
# unresolvable one is dropped.
_MIXED_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Mixed</title>
  <item><title>Dated</title><link>https://example.com/dated</link>
    <pubDate>Mon, 02 Jun 2026 10:00:00 GMT</pubDate></item>
  <item><title>Undated</title><link>https://example.com/undated</link></item>
  <item><title>Bad link</title><link>#frag</link></item>
</channel></rss>"""


def test_published_at_degrades_malformed_date_to_none() -> None:
    # A struct_time carrying a non-numeric field makes calendar.timegm raise; the
    # entry must keep parsing as undated rather than the whole feed crashing
    # (never-lost, REQ-007/REQ-009). A None field is skipped via the falsy guard.
    good = SimpleNamespace(published_parsed=time.gmtime(0), updated_parsed=None)
    assert _published_at(good) == 0

    malformed = SimpleNamespace(
        published_parsed=time.struct_time(("x", 0, 0, 0, 0, 0, 0, 0, 0)),
        updated_parsed=None,
    )
    assert _published_at(malformed) is None

    assert _published_at(SimpleNamespace()) is None


def test_undated_entries_sort_to_tail_and_bad_links_drop() -> None:
    entries = parse_feed(_MIXED_RSS, "https://example.com/")

    assert [e.canonical_url for e in entries] == [
        "https://example.com/dated",
        "https://example.com/undated",
    ]
    assert entries[0].published_at is not None
    assert entries[1].published_at is None
