"""Behavior tests for per-site gathering (TASK-030).

Network-free via ``httpx.MockTransport``; the seen-store is a real tmp_path
SQLite db so the seen-filtering and "records nothing on error" contracts are
exercised against actual storage.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

import httpx

from feed_filter.canonical import canonical_url
from feed_filter.config import DEFAULT_PER_SITE_CAP
from feed_filter.pipeline import gather_new
from feed_filter.seen import count, open_db, snapshot
from feed_filter.sites import SiteConfig

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _rss(n: int) -> str:
    items = "".join(
        f"<item><title>Post {i}</title><link>https://example.com/posts/{i}</link></item>"
        for i in range(n)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>F</title>'
        f"<link>https://example.com/</link>{items}</channel></rss>"
    )


def _index(paths: list[str]) -> str:
    links = "".join(f'<a href="{p}">x</a>' for p in paths)
    return f"<!doctype html><html><body><main>{links}</main></body></html>"


_FEED = SiteConfig(id="f1", name="Feed", feed_url="https://example.com/feed.xml")
_SCRAPE = SiteConfig(
    id="s1",
    name="Scrape",
    index_url="https://example.com/blog",
    article_url_pattern=r"^/blog/[^/]+/?$",
)


def test_filters_already_seen(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_rss(3), headers={"content-type": "application/rss+xml"})

    with contextlib.closing(open_db(tmp_path / "db")) as conn:
        snapshot(conn, "f1", [canonical_url("https://example.com/posts/1")])
        with _client(handler) as client:
            result = gather_new(conn, _FEED, client=client)

    urls = [str(e.canonical_url) for e in result.entries]
    assert result.index_matches == 3
    assert "https://example.com/posts/1" not in urls
    assert urls == ["https://example.com/posts/0", "https://example.com/posts/2"]
    assert result.error is None
    assert result.zero_links is False


def test_per_site_cap_clamps(tmp_path: Path) -> None:
    n = DEFAULT_PER_SITE_CAP + 5

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_rss(n), headers={"content-type": "application/rss+xml"})

    with contextlib.closing(open_db(tmp_path / "db")) as conn, _client(handler) as client:
        result = gather_new(conn, _FEED, client=client)

    assert result.index_matches == n
    assert len(result.entries) == DEFAULT_PER_SITE_CAP


def test_broken_pattern_sets_zero_links(tmp_path: Path) -> None:
    # Stored pattern matches nothing on the live page → the self-heal signal.
    def handler(_: httpx.Request) -> httpx.Response:
        html = _index(["/news/a", "/news/b", "/about"])
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    with contextlib.closing(open_db(tmp_path / "db")) as conn, _client(handler) as client:
        result = gather_new(conn, _SCRAPE, client=client)

    assert result.index_matches == 0
    assert result.zero_links is True
    assert result.entries == []


def test_quiet_healthy_scrape_is_not_zero_links(tmp_path: Path) -> None:
    # Pattern still matches (index_matches > 0); everything just happens to be
    # seen already. That is a quiet day, NOT a broken pattern — no heal.
    def handler(_: httpx.Request) -> httpx.Response:
        html = _index(["/blog/a", "/blog/b"])
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    with contextlib.closing(open_db(tmp_path / "db")) as conn:
        snapshot(
            conn,
            "s1",
            [
                canonical_url("https://example.com/blog/a"),
                canonical_url("https://example.com/blog/b"),
            ],
        )
        with _client(handler) as client:
            result = gather_new(conn, _SCRAPE, client=client)

    assert result.index_matches == 2
    assert result.zero_links is False
    assert result.entries == []


def test_fetch_error_sets_error_and_records_nothing(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with contextlib.closing(open_db(tmp_path / "db")) as conn:
        with _client(handler) as client:
            result = gather_new(conn, _FEED, client=client)
        assert count(conn) == 0  # nothing recorded seen (REQ-008)

    assert result.error is not None
    assert result.entries == []
    assert result.index_matches == 0
    assert result.zero_links is False
