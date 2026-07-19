"""Tests for queue-capture URL/title extraction."""

from __future__ import annotations

from kboat.queue.parse import parse_capture


def test_plain_capture() -> None:
    cap = parse_capture("[Some Title](https://example.com/article)\n")
    assert cap.url == "https://example.com/article"
    assert cap.title == "Some Title"


def test_url_with_parentheses_survives() -> None:
    # A URL containing parens (a Wikipedia disambiguation link) must stay whole:
    # the extraction anchors on the FINAL ')' , not the first.
    cap = parse_capture("[Mercury](https://en.wikipedia.org/wiki/Mercury_(planet))")
    assert cap.url == "https://en.wikipedia.org/wiki/Mercury_(planet)"
    assert cap.title == "Mercury"


def test_title_crafted_with_link_cannot_redirect_url() -> None:
    # A malicious page title embeds its own `](evil)`; anchoring on the LAST `](`
    # extracts the real trailing URL the bookmarklet appended, not the injected one.
    cap = parse_capture("[x](https://evil.example)](https://real.example/page)")
    assert cap.url == "https://real.example/page"


def test_frontmatter_is_stripped() -> None:
    cap = parse_capture("---\ntags: [x]\n---\n\n[T](https://example.com)\n")
    assert cap.url == "https://example.com"
    assert cap.title == "T"


def test_non_http_scheme_is_not_a_url() -> None:
    # Only http(s) captures are ingestable; a mailto link is not a URL here.
    assert parse_capture("[mail](mailto:x@example.com)").url is None


def test_body_without_link_is_malformed() -> None:
    cap = parse_capture("just some text, no link\n")
    assert cap.url is None
    assert cap.title == ""


def test_empty_link_target_is_malformed() -> None:
    assert parse_capture("[title]()").url is None


def test_url_with_internal_space_is_rejected() -> None:
    # A well-formed URL has no whitespace; a spaced target is malformed.
    assert parse_capture("[t](https://example.com/a b)").url is None
