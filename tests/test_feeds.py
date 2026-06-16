"""Behavior tests for feed parsing (TASK-018)."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from feed_filter.feeds import _published_at, html_to_text, parse_feed

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


# Atom feed with a root-relative entry link, kept inline (not a fixture file) so
# the relative link documents resolution without becoming a dangling link for the
# repo's link checker — the same reason test_discover builds relative hrefs inline.
_ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Example</title>
  <link href="https://atom.example.com/"/>
  <entry>
    <title>Atom Entry One</title>
    <link href="/articles/one"/>
    <summary>First atom entry.</summary>
    <updated>2026-06-01T12:00:00Z</updated>
    <id>urn:uuid:0001</id>
  </entry>
</feed>"""


def test_atom_resolves_relative_links() -> None:
    entries = parse_feed(_ATOM, "https://atom.example.com/")

    assert len(entries) == 1
    entry = entries[0]
    assert entry.canonical_url == "https://atom.example.com/articles/one"
    assert entry.title == "Atom Entry One"
    assert entry.summary == "First atom entry."


def test_malformed_feed_returns_empty() -> None:
    assert parse_feed(b"<<< this is not a feed", "https://example.com/") == []


def test_html_to_text_flattens_strips_and_passes_plain_text_through() -> None:
    # Block boundaries become whitespace; inline byline links stay intact.
    html = "<p>By <a href='x'>Ann</a>, <a href='y'>Bob</a></p><p>Intro &amp; body.</p>"
    assert html_to_text(html) == "By Ann, Bob Intro & body."
    # script/style content is dropped, not flattened into the text.
    assert html_to_text("<style>.a{}</style><p>Real</p><script>x=1</script>") == "Real"
    # Markup-free, entity-free plain text passes through unchanged (the common
    # plain-description RSS case); this is the only "unchanged" guarantee — text
    # carrying literal < > or & is HTML by contract and may be rewritten.
    assert html_to_text("Summary of first post.") == "Summary of first post."
    # Empty / tag-only / None collapse to None so downstream sees "no summary".
    assert html_to_text("") is None
    assert html_to_text("<p></p>") is None
    assert html_to_text(None) is None


# RSS whose item body is HTML in <content:encoded> (Medium/Substack shape): the
# parser must hand downstream plain prose, not the raw markup.
_HTML_RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Full-text</title>
  <item><title>Deep Dive</title><link>https://example.com/deep</link>
    <pubDate>Mon, 02 Jun 2026 10:00:00 GMT</pubDate>
    <content:encoded><![CDATA[<p>By <a href="/a">Author</a></p><p>Real architectural prose here.</p>]]></content:encoded>
  </item>
</channel></rss>"""


def test_html_content_summary_is_flattened_to_text() -> None:
    entries = parse_feed(_HTML_RSS, "https://example.com/")
    assert len(entries) == 1
    assert entries[0].summary == "By Author Real architectural prose here."


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


# Feed with entries in non-date order (oldest first) to verify sort=False.
# The Jun 01 item is first in source but would be last under date-desc sort.
_RANK_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Rank</title>
  <item><title>Oldest</title><link>https://example.com/oldest</link>
    <pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
  <item><title>Middle</title><link>https://example.com/middle</link>
    <pubDate>Fri, 12 Jun 2026 10:00:00 GMT</pubDate></item>
  <item><title>Newest</title><link>https://example.com/newest</link>
    <pubDate>Sun, 14 Jun 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_sort_false_preserves_feed_source_order() -> None:
    entries = parse_feed(_RANK_RSS, "https://example.com/", sort=False)
    assert [e.canonical_url for e in entries] == [
        "https://example.com/oldest",
        "https://example.com/middle",
        "https://example.com/newest",
    ]


def test_sort_true_orders_by_date_descending() -> None:
    entries = parse_feed(_RANK_RSS, "https://example.com/", sort=True)
    assert [e.canonical_url for e in entries] == [
        "https://example.com/newest",
        "https://example.com/middle",
        "https://example.com/oldest",
    ]
