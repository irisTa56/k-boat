"""Behavior tests for per-site gathering (TASK-030).

Network-free via ``httpx.MockTransport``; the seen-store is a real tmp_path
SQLite db so the seen-filtering and "records nothing on error" contracts are
exercised against actual storage.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from _fake_playwright import FakeContext, FakeResponse, install_fake_playwright

from feed_filter import browser
from feed_filter.canonical import canonical_url
from feed_filter.config import DEFAULT_PER_SITE_CAP
from feed_filter.pipeline import gather_new
from feed_filter.seen import count, open_db, snapshot
from feed_filter.sites import SiteConfig

Handler = Callable[[httpx.Request], httpx.Response]


def _no_http(_: httpx.Request) -> httpx.Response:
    """A client handler that must never fire — the browser branch ignores it (F2)."""
    raise AssertionError("browser path must not use the httpx client")


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


# --- browser gather path (requires_browser) -------------------------------


@pytest.fixture
def browser_env() -> Iterator[None]:
    """Cold browser singleton for each browser-path test."""
    browser._bundle = None
    yield
    browser._bundle = None


_BROWSER_FEED = SiteConfig(
    id="bf", name="JS Feed", feed_url="https://example.com/feed.xml", requires_browser=True
)
_BROWSER_SCRAPE = SiteConfig(
    id="bs",
    name="JS Scrape",
    index_url="https://example.com/blog",
    article_url_pattern=r"^/blog/[^/]+/?$",
    requires_browser=True,
)


def test_browser_feed_yields_same_entries_as_httpx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser_env: None
) -> None:
    # REQ-002: the browser feed path must produce an identical Entry list to the
    # httpx path over the same bytes. Establish the httpx baseline, then route the
    # same feed through the browser (fake) and compare.
    rss = _rss(3)

    def serve(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=rss, headers={"content-type": "application/rss+xml"})

    httpx_feed = SiteConfig(id="hf", name="HF", feed_url="https://example.com/feed.xml")
    with contextlib.closing(open_db(tmp_path / "a")) as conn, _client(serve) as client:
        baseline = gather_new(conn, httpx_feed, client=client)

    resp = FakeResponse(status=200, url="https://example.com/feed.xml", body=rss.encode())
    install_fake_playwright(monkeypatch, context=FakeContext(response=resp))
    with contextlib.closing(open_db(tmp_path / "b")) as conn, _client(_no_http) as client:
        gathered = gather_new(conn, _BROWSER_FEED, client=client)

    assert [str(e.canonical_url) for e in gathered.entries] == [
        str(e.canonical_url) for e in baseline.entries
    ]
    assert [e.title for e in gathered.entries] == [e.title for e in baseline.entries]
    assert gathered.index_matches == baseline.index_matches
    assert gathered.error is None


def test_browser_scrape_routes_through_fetch_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser_env: None
) -> None:
    ctx = FakeContext(html=_index(["/blog/a", "/blog/b"]), response=FakeResponse(status=200))
    install_fake_playwright(monkeypatch, context=ctx)
    with contextlib.closing(open_db(tmp_path / "db")) as conn, _client(_no_http) as client:
        gathered = gather_new(conn, _BROWSER_SCRAPE, client=client)

    assert [str(e.canonical_url) for e in gathered.entries] == [
        "https://example.com/blog/a",
        "https://example.com/blog/b",
    ]
    assert all(e.kind == "scrape" for e in gathered.entries)
    # The rendered index URL was the navigation target.
    assert ctx.goto_calls[0].url == "https://example.com/blog"


def test_browser_scrape_resolves_links_against_post_redirect_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser_env: None
) -> None:
    # The index redirects example.com/blog -> www.example.com/blog; the rendered
    # page's relative link must resolve against the post-redirect host (REQ-002,
    # symmetric with the httpx path), not the configured index_url. If the base were
    # the configured index_url, the canonical URL would carry the bare host instead.
    ctx = FakeContext(
        html=_index(["/blog/a"]),
        response=FakeResponse(status=200, url="https://www.example.com/blog"),
    )
    install_fake_playwright(monkeypatch, context=ctx)
    with contextlib.closing(open_db(tmp_path / "db")) as conn, _client(_no_http) as client:
        gathered = gather_new(conn, _BROWSER_SCRAPE, client=client)

    assert [str(e.canonical_url) for e in gathered.entries] == ["https://www.example.com/blog/a"]


def test_browser_fetch_error_sets_error_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser_env: None
) -> None:
    # A BrowserFetchError absorbs into the per-site error exactly like FetchError
    # (REQ-008): empty entries, nothing recorded seen, retried next run.
    install_fake_playwright(monkeypatch, context=FakeContext(response=FakeResponse(status=403)))
    with contextlib.closing(open_db(tmp_path / "db")) as conn:
        with _client(_no_http) as client:
            gathered = gather_new(conn, _BROWSER_FEED, client=client)
        assert count(conn) == 0

    assert gathered.error is not None
    assert "HTTP 403" in gathered.error
    assert gathered.entries == []
    assert gathered.index_matches == 0
