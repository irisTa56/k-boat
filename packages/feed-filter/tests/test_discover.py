"""Behavior tests for the 4-layer discovery.

Network-free: every client is built over an ``httpx.MockTransport`` whose handler
routes by path so the self-feed / alternate-link / typical-path / clustering and
rejection paths are exercised without touching the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from feed_filter.discover import discover
from feed_filter.fetch import FetchError

FIXTURES = Path(__file__).parent / "fixtures"

Route = tuple[int, bytes, str]  # status, body, content-type


def _client(routes: dict[str, Route]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        route = routes.get(request.url.path)
        if route is None:
            return httpx.Response(404)
        status, body, content_type = route
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _rss() -> bytes:
    return (FIXTURES / "rss.xml").read_bytes()


def _links_html(hrefs: Sequence[str]) -> bytes:
    body = "".join(f'<a href="{h}">x</a>' for h in hrefs)
    return f"<html><body>{body}</body></html>".encode()


def _index_html() -> bytes:
    return (FIXTURES / "discover_index.html").read_bytes()


def test_layer_a_url_is_feed() -> None:
    client = _client({"/feed": (200, _rss(), "application/rss+xml")})
    with client:
        result = discover("https://example.com/feed", client=client)

    assert result.rejection is None
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.feed_type == "feed"
    assert candidate.feed_url == "https://example.com/feed"
    assert candidate.entry_count == 2
    # parse_feed orders newest-first; Second Post has the later pubDate.
    assert candidate.sample_urls == (
        "https://example.com/posts/second",
        "https://example.com/posts/first",
    )
    assert candidate.index_url == "" and candidate.article_url_pattern == ""


def test_layer_b_alternate_link_resolved() -> None:
    page = (
        b'<html><head><link rel="alternate" type="application/rss+xml" '
        b'href="/feed.xml"></head><body>hi</body></html>'
    )
    client = _client(
        {
            "/": (200, page, "text/html"),
            "/feed.xml": (200, _rss(), "application/rss+xml"),
        }
    )
    with client:
        result = discover("https://example.com/", client=client)

    assert result.rejection is None
    assert [c.feed_url for c in result.candidates] == ["https://example.com/feed.xml"]
    assert result.candidates[0].feed_type == "feed"


def test_non_feed_alternate_link_ignored() -> None:
    # A rel="alternate" without a feed type (e.g. an hreflang alternate) must not
    # be seeded as a feed; with no real feed anywhere, discovery falls through.
    page = (
        b'<html><head><link rel="alternate" hreflang="fr" href="/fr/"></head>'
        b"<body><p>loading...</p></body></html>"
    )
    client = _client({"/": (200, page, "text/html")})
    with client:
        result = discover("https://example.com/", client=client)

    assert all(c.feed_type != "feed" for c in result.candidates)
    assert result.rejection is not None and result.rejection.reason == "needs_js"


def test_alternate_link_with_charset_param_accepted() -> None:
    # A valid feed declaration whose type carries a `; charset=...` parameter must
    # still be recognized (the parameter is stripped before matching).
    page = (
        b'<html><head><link rel="alternate" '
        b'type="application/rss+xml; charset=utf-8" href="/feed.xml"></head>'
        b"<body>hi</body></html>"
    )
    client = _client(
        {
            "/": (200, page, "text/html"),
            "/feed.xml": (200, _rss(), "application/rss+xml"),
        }
    )
    with client:
        result = discover("https://example.com/", client=client)

    assert [c.feed_url for c in result.candidates] == ["https://example.com/feed.xml"]


def test_non_feed_alternates_absorbed_and_not_reprobed() -> None:
    # A self-referential alternate (resolves to the already-fetched initial URL)
    # and an alternate pointing at a 200 non-feed body are both absorbed; neither
    # the initial URL nor the dead alternate is fetched a second time (it is
    # already in `attempted` when typical-path probing would reach it).
    page = (
        b"<html><head>"
        b'<link rel="alternate" type="application/rss+xml" href="/">'
        b'<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        b"</head><body><p>loading...</p></body></html>"
    )
    counts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        counts[path] = counts.get(path, 0) + 1
        if path == "/":
            return httpx.Response(200, content=page, headers={"content-type": "text/html"})
        if path == "/feed.xml":
            return httpx.Response(200, content=b"<html>not a feed</html>", headers={})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with client:
        result = discover("https://example.com/", client=client)

    assert result.rejection is not None and result.rejection.reason == "needs_js"
    assert counts["/"] == 1
    assert counts["/feed.xml"] == 1


def test_layer_c_typical_path_probe() -> None:
    # No alternate link declared; discovery must probe typical paths and find the
    # feed at /feed.xml. Unrelated probes (e.g. /rss.xml) 404 and are absorbed.
    page = b"<html><body><p>no feed link here</p></body></html>"
    client = _client(
        {
            "/": (200, page, "text/html"),
            "/feed.xml": (200, _rss(), "application/rss+xml"),
        }
    )
    with client:
        result = discover("https://example.com/", client=client)

    assert result.rejection is None
    assert [c.feed_url for c in result.candidates] == ["https://example.com/feed.xml"]


def test_layer_d_clusters_article_dropping_nav() -> None:
    client = _client({"/blog": (200, _index_html(), "text/html")})
    with client:
        result = discover("https://example.com/blog", client=client)

    assert result.rejection is None
    # Two article clusters survive (/blog descendant first, then /tag); the
    # top-level /<slug> nav cluster is dropped.
    assert all(c.feed_type == "scrape" for c in result.candidates)
    patterns = [c.article_url_pattern for c in result.candidates]
    assert patterns == [r"^/blog/[^/]+/?$", r"^/tag/[^/]+/?$"]
    assert r"^/[^/]+/?$" not in patterns

    blog = result.candidates[0]
    assert blog.index_url == "https://example.com/blog"
    assert blog.entry_count == 5
    assert blog.feed_url == ""
    # Query strings are stripped before clustering, so the utm'd link collapses
    # into the same cluster with a clean sample URL.
    assert "https://example.com/blog/post-five" in blog.sample_urls
    assert len(blog.sample_urls) == 5


def test_rejection_no_article_clusters() -> None:
    # Index URL is two levels deep but the only qualifying cluster is top-level
    # navigation (shallower than the index) — actionable: wrong page.
    page = (
        b"<html><body>"
        b'<a href="/about">a</a><a href="/contact">b</a><a href="/pricing">c</a>'
        b'<a href="/docs">d</a><a href="/home">e</a>'
        b"</body></html>"
    )
    client = _client({"/blog/tech": (200, page, "text/html")})
    with client:
        result = discover("https://example.com/blog/tech", client=client)

    assert result.candidates == ()
    assert result.rejection is not None
    assert result.rejection.reason == "no_article_clusters"


def test_rejection_needs_js_sparse_html() -> None:
    page = b"<html><body><p>loading...</p></body></html>"
    client = _client({"/": (200, page, "text/html")})
    with client:
        result = discover("https://example.com/", client=client)

    assert result.candidates == ()
    assert result.rejection is not None
    assert result.rejection.reason == "needs_js"


def test_rejection_needs_js_non_html() -> None:
    client = _client({"/": (200, b'{"items": []}', "application/json")})
    with client:
        result = discover("https://example.com/", client=client)

    assert result.candidates == ()
    assert result.rejection is not None
    assert result.rejection.reason == "needs_js"


def test_multiple_feed_candidates_dedup_on_redirect() -> None:
    # Two declared feeds surface as two ordered candidates; a third whose probe
    # redirects onto the first collapses (dedup is on the post-redirect URL).
    page = (
        b"<html><head>"
        b'<link rel="alternate" type="application/rss+xml" href="/a.xml">'
        b'<link rel="alternate" type="application/rss+xml" href="/b.xml">'
        b'<link rel="alternate" type="application/rss+xml" href="/c.xml">'
        b"</head><body>hi</body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/":
            return httpx.Response(200, content=page, headers={"content-type": "text/html"})
        if path == "/c.xml":
            return httpx.Response(301, headers={"location": "https://example.com/a.xml"})
        if path in ("/a.xml", "/b.xml"):
            return httpx.Response(
                200, content=_rss(), headers={"content-type": "application/rss+xml"}
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with client:
        result = discover("https://example.com/", client=client)

    assert result.rejection is None
    assert [c.feed_url for c in result.candidates] == [
        "https://example.com/a.xml",
        "https://example.com/b.xml",
    ]


def test_feed_url_is_post_redirect_final_url() -> None:
    # The candidate's feed_url must be the URL after redirects (the load-bearing
    # reason validation keys off final_url, not the requested URL).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/feed":
            return httpx.Response(301, headers={"location": "https://example.com/feed/"})
        return httpx.Response(200, content=_rss(), headers={"content-type": "application/rss+xml"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with client:
        result = discover("https://example.com/feed", client=client)

    assert [c.feed_url for c in result.candidates] == ["https://example.com/feed/"]


def test_candidate_probe_fetch_error_is_absorbed() -> None:
    # Complement of the initial-URL raise: a probe that fails transport-wise is
    # absorbed (not a feed), and discovery falls through rather than raising.
    page = (
        b'<html><head><link rel="alternate" type="application/rss+xml" '
        b'href="/broken.xml"></head><body><p>loading...</p></body></html>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, content=page, headers={"content-type": "text/html"})
        if request.url.path == "/broken.xml":
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with client:
        result = discover("https://example.com/", client=client)

    assert result.candidates == ()
    assert result.rejection is not None and result.rejection.reason == "needs_js"


def test_descendant_cluster_ordered_before_alphabetically_earlier_sibling() -> None:
    # Both clusters are size 5 and survive nav-drop; /news is the descendant of
    # the index URL, /article is not. Descendant-first must win over key-asc
    # (which would otherwise put /article first).
    hrefs = [f"/article/{s}" for s in "abcde"] + [f"/news/{s}" for s in "abcde"]
    client = _client({"/news": (200, _links_html(hrefs), "text/html")})
    with client:
        result = discover("https://example.com/news", client=client)

    assert result.rejection is None
    assert [c.article_url_pattern for c in result.candidates] == [
        r"^/news/[^/]+/?$",
        r"^/article/[^/]+/?$",
    ]


def test_cluster_counts_distinct_canonical_urls() -> None:
    # /blog/a and /blog/a/ are one article under canonical dedup, the
    # same key scrape_index uses — so the cluster of 6 raw links counts as 5 and
    # the preview shows the canonical form once, matching what registration
    # ingests rather than over-counting.
    hrefs = ["/blog/a", "/blog/a/"] + [f"/blog/{s}" for s in "bcde"]
    client = _client({"/blog": (200, _links_html(hrefs), "text/html")})
    with client:
        result = discover("https://example.com/blog", client=client)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.entry_count == 5
    assert candidate.sample_urls.count("https://example.com/blog/a") == 1


def test_cluster_qualification_uses_canonical_count() -> None:
    # Five raw links that canonicalize to four distinct articles fall below the
    # min cluster size — proving the >=5 threshold counts canonical URLs, not raw
    # hrefs (a raw count of 5 would wrongly qualify it as a scrape candidate).
    hrefs = ["/blog/a", "/blog/a/"] + [f"/blog/{s}" for s in "bcd"]
    client = _client({"/blog": (200, _links_html(hrefs), "text/html")})
    with client:
        result = discover("https://example.com/blog", client=client)

    assert result.candidates == ()
    assert result.rejection is not None and result.rejection.reason == "needs_js"


def test_unreachable_host_raises_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with client, pytest.raises(FetchError):
        discover("https://nope.example.com/", client=client)


def test_layer_c_caps_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Guard the probe cap: count how many distinct paths discovery fetches when
    # nothing validates. A non-feed HTML root with no clusters must still bound
    # the probe fan-out to the typical-path cap (+1 for the initial fetch).
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, content=b"<html><body>x</body></html>", headers={})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with client:
        discover("https://example.com/", client=client)

    # 1 initial + at most the typical-path cap of distinct probes.
    assert len(fetched) <= 1 + 12
    assert "/feed.xml" in fetched
