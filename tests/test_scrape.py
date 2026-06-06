"""Behavior tests for index scraping (TASK-020)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from feed_filter.scrape import scrape_index

FIXTURES = Path(__file__).parent / "fixtures"
ARTICLE_PATTERN = r"^/blog/[^/]+/?$"


def _html() -> str:
    return (FIXTURES / "index.html").read_text()


def test_matches_article_links_excludes_nav_and_cross_host() -> None:
    entries = scrape_index(_html(), "https://blog.example.com/", ARTICLE_PATTERN)

    # /about, /contact fail the pattern; the other.example.com link is cross-host. The
    # duplicate first-post (with tracking query) collapses to one entry.
    assert [e.canonical_url for e in entries] == [
        "https://blog.example.com/blog/first-post",
        "https://blog.example.com/blog/second-post",
        "https://blog.example.com/blog/third-post",
    ]
    assert all(e.kind == "scrape" and e.title is None and e.summary is None for e in entries)


def test_dedupes_repeated_article() -> None:
    entries = scrape_index(_html(), "https://blog.example.com/", r"^/blog/first-post$")
    assert [e.canonical_url for e in entries] == ["https://blog.example.com/blog/first-post"]


def test_respects_cap() -> None:
    entries = scrape_index(_html(), "https://blog.example.com/", ARTICLE_PATTERN, max_entries=2)
    assert len(entries) == 2


def test_dedupes_canonical_variants() -> None:
    # The same article linked as bare, trailing-slash, and mixed-host-case must
    # collapse to one entry keyed by the canonical URL (PAT-002) — not three the
    # seen-store would later treat as one, double-counting the cap and reminder.
    html = (
        '<a href="https://blog.example.com/blog/x">a</a>'
        '<a href="https://blog.example.com/blog/x/">b</a>'
        '<a href="https://Blog.Example.com/blog/x">c</a>'
    )
    entries = scrape_index(html, "https://blog.example.com/", ARTICLE_PATTERN)
    assert [e.canonical_url for e in entries] == ["https://blog.example.com/blog/x"]


def test_matches_double_slash_variant_via_canonical_path() -> None:
    # A `/blog//x` link points at the same article as `/blog/x`; canonicalization
    # collapses the `//`, so matching on the canonical path keeps it (and dedupes
    # it against the clean variant) instead of skipping it on the raw path. This
    # keeps scrape symmetric with discovery, which counts the canonical form.
    html = '<a href="/blog//x">a</a><a href="/blog/x">b</a>'
    entries = scrape_index(html, "https://blog.example.com/", ARTICLE_PATTERN)
    assert [e.canonical_url for e in entries] == ["https://blog.example.com/blog/x"]


def test_invalid_pattern_propagates() -> None:
    # A broken pattern must raise, not return [] — Phase 5 distinguishes a
    # 0-match index (REQ-006 self-heal signal) from a quiet healthy day, and a
    # swallowed re.error would disguise a corrupt pattern as the latter.
    with pytest.raises(re.error):
        scrape_index(_html(), "https://blog.example.com/", "[unclosed")
