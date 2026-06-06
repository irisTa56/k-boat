"""Table-driven tests for canonical URL normalization (TASK-009)."""

from __future__ import annotations

import pytest

from feed_filter.canonical import canonical_url, resolve_link

# (url, base, expected)
CASES: list[tuple[str, str | None, str]] = [
    # relative resolution against a base
    ("/posts/1", "https://example.com/blog", "https://example.com/posts/1"),
    ("post", "https://example.com/blog/", "https://example.com/blog/post"),
    # absolute url, no base
    ("https://example.com/a", None, "https://example.com/a"),
    # scheme + host lowercased, path case preserved
    ("HTTPS://Example.COM/Path", None, "https://example.com/Path"),
    # default ports dropped, non-default kept
    ("http://example.com:80/a", None, "http://example.com/a"),
    ("https://example.com:443/a", None, "https://example.com/a"),
    ("http://example.com:8080/a", None, "http://example.com:8080/a"),
    # userinfo case-preserved (case-sensitive), only the host is lowercased
    ("https://User:PassWORD@Example.COM/a", None, "https://User:PassWORD@example.com/a"),
    ("https://User@Example.COM/a", None, "https://User@example.com/a"),
    # IPv6 host literal keeps its brackets; default port still dropped
    ("https://[::1]:8080/path", None, "https://[::1]:8080/path"),
    ("https://[::1]:443/path", None, "https://[::1]/path"),
    # fragment dropped
    ("https://example.com/a#section", None, "https://example.com/a"),
    # tracking params stripped, content params kept
    ("https://example.com/a?utm_source=x&utm_medium=y&id=5", None, "https://example.com/a?id=5"),
    ("https://example.com/a?gclid=abc", None, "https://example.com/a"),
    ("https://example.com/a?fbclid=abc&mc_cid=1", None, "https://example.com/a"),
    # ref is content-bearing and deliberately NOT stripped (REQ-009 never-lost)
    ("https://example.com/a?ref=twitter", None, "https://example.com/a?ref=twitter"),
    # query params sorted so order can't fork the key
    ("https://example.com/a?b=2&a=1", None, "https://example.com/a?a=1&b=2"),
    # percent-encoding hex upper-cased
    ("https://example.com/a%2fb", None, "https://example.com/a%2Fb"),
    # trailing slash normalized (root keeps its slash)
    ("https://example.com/blog/", None, "https://example.com/blog"),
    ("https://example.com", None, "https://example.com/"),
    ("https://example.com/", None, "https://example.com/"),
    # duplicate slashes collapsed
    ("https://example.com/a//b///c", None, "https://example.com/a/b/c"),
    # trailing slash + query together
    ("https://example.com/a/?id=5", None, "https://example.com/a?id=5"),
]


@pytest.mark.parametrize(("url", "base", "expected"), CASES)
def test_canonical(url: str, base: str | None, expected: str) -> None:
    assert canonical_url(url, base) == expected


@pytest.mark.parametrize(("url", "base", "expected"), CASES)
def test_idempotent(url: str, base: str | None, expected: str) -> None:
    once = canonical_url(url, base)
    assert canonical_url(once) == once


def test_default_and_explicit_port_dedupe_identically() -> None:
    # The core point of port normalization: implicit and explicit default ports
    # must produce the same dedupe key.
    assert canonical_url("https://example.com/a") == canonical_url("https://example.com:443/a")


def test_query_order_dedupes_identically() -> None:
    assert canonical_url("https://example.com/a?x=1&y=2") == canonical_url(
        "https://example.com/a?y=2&x=1"
    )


def test_distinct_articles_stay_distinct() -> None:
    a = canonical_url("https://example.com/blog/alpha")
    b = canonical_url("https://example.com/blog/beta")
    assert a != b


def test_resolve_link_resolves_relative_and_absolute() -> None:
    assert resolve_link("https://example.com/blog/", "post") == "https://example.com/blog/post"
    assert (
        resolve_link("https://example.com/", "https://other.example.com/a")
        == "https://other.example.com/a"
    )


@pytest.mark.parametrize(
    "href", [None, "", "   ", "#section", "javascript:void(0)", "mailto:a@b.c"]
)
def test_resolve_link_rejects_non_article_hrefs(href: str | None) -> None:
    assert resolve_link("https://example.com/", href) is None


def test_resolve_link_same_host_guard() -> None:
    # Cross-host is allowed by default (feed syndication) but rejected under the
    # scrape path's same-host guard.
    off_host = "https://other.example.com/a"
    assert resolve_link("https://example.com/", off_host) == off_host
    assert resolve_link("https://example.com/", off_host, require_same_host=True) is None
