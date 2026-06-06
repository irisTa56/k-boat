"""End-to-end CLI smoke over real state files (TASK-038, TEST-011).

Exercises the actual pipeline — fetch → parse → seen-store → round-robin/cap —
not the monkeypatched core fns of ``test_cli.py``. The only fakes are the network
(an ``httpx.MockTransport`` serving a controllable RSS body) and ``rem`` (an
injected runner). The seen-store and ``sites.toml`` are real tmp files via the
``state_dir`` fixture, so the cross-process invariants hold against persisted
state:

- ``add-site`` snapshots the back-catalog seen *before* writing config (REQ-002),
  so ``new-entries`` returns only entries that appeared *after* registration;
- ``remind`` creates the reminder *and* records seen in one process (REQ-009),
  so a second ``new-entries`` no longer yields the reminded entry — no duplicate.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from _fake_playwright import FakeContext, FakeResponse, install_fake_playwright

from feed_filter import browser, cli
from feed_filter import reminders as reminders_mod
from feed_filter.canonical import canonical_url
from feed_filter.config import db_path, sites_path
from feed_filter.seen import is_seen, open_db
from feed_filter.sites import load_sites


def _rss(*links: tuple[str, str]) -> bytes:
    """Build a minimal RSS 2.0 body from (title, link) pairs."""
    items = "".join(
        f"<item><title>{title}</title><link>{link}</link></item>" for title, link in links
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>Example</title>'
        f"{items}</channel></rss>"
    )
    return body.encode("utf-8")


def _index_html(*paths: str) -> bytes:
    """Build an index page: a nav link plus one article link per path."""
    links = "".join(f'<a href="{p}">x</a>' for p in paths)
    return f"<html><body><nav><a href='/'>home</a></nav>{links}</body></html>".encode()


class _MockSite:
    """A mutable response body the MockTransport serves; the test rewrites it.

    Every request (feed fetch, index fetch, heal re-scrape) returns the current
    ``body`` — the tests drive time forward by reassigning ``body`` between CLI
    invocations, exactly as a live site changes between runs.
    """

    def __init__(self, body: bytes, content_type: str = "application/rss+xml") -> None:
        self.body = body
        self.content_type = content_type

    def client(self) -> httpx.Client:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=self.body, headers={"content-type": self.content_type}
            )

        return httpx.Client(transport=httpx.MockTransport(handler))


def _out(capsys: pytest.CaptureFixture[str]) -> Any:
    return json.loads(capsys.readouterr().out)


def _seen(url: str) -> bool:
    with contextlib.closing(open_db(db_path())) as conn:
        return is_seen(conn, canonical_url(url))


FEED_URL = "https://example.com/feed.xml"
A = ("A", "https://example.com/a")
B = ("B", "https://example.com/b")
C = ("C", "https://example.com/c")


def test_add_site_then_run_has_no_duplicate(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feed = _MockSite(_rss(A, B))
    monkeypatch.setattr(cli, "build_client", feed.client)

    # Fake rem: a successful runner so the real add_reminder logic runs (argv
    # build + JSON id parse), and cmd_remind's real seen.record follows it.
    rem_calls: list[list[str]] = []

    def fake_runner(argv: list[str], **_: Any) -> SimpleNamespace:
        rem_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "rem-1"}), stderr="")

    monkeypatch.setattr(
        cli,
        "add_reminder",
        lambda title, url, notes: reminders_mod.add_reminder(title, url, notes, runner=fake_runner),
    )

    # --- register: snapshots A and B seen, THEN writes sites.toml -------------
    assert cli.main(["add-site", "--id", "ex", "--name", "Example", "--feed-url", FEED_URL]) == 0
    added = _out(capsys)
    assert added == {"site_id": "ex", "kind": "feed", "snapshotted": 2}
    assert _seen(A[1]) and _seen(B[1])  # back-catalog snapshotted before any run
    assert (state_dir / "sites.toml").exists()  # config written last

    # --- a new entry C appears after registration ----------------------------
    feed.body = _rss(A, B, C)
    assert cli.main(["new-entries"]) == 0
    gathered = _out(capsys)
    assert [e["url"] for e in gathered["entries"]] == [C[1]]  # only the post-snapshot item
    assert gathered["sites"] == [{"site_id": "ex", "zero_links": False, "error": None}]

    # --- remind C: creates the reminder AND records it seen, atomically -------
    assert (
        cli.main(["remind", "--site-id", "ex", "--url", C[1], "--title", "C", "--notes", "note"])
        == 0
    )
    reminded = _out(capsys)
    assert reminded["id"] == "rem-1"
    assert reminded["kept"] is True
    assert len(rem_calls) == 1 and rem_calls[0][:2] == ["rem", "add"]
    assert _seen(C[1])  # recorded in the same process as the remind

    # --- second run: C is gone, nothing duplicated ---------------------------
    assert cli.main(["new-entries"]) == 0
    again = _out(capsys)
    assert again["entries"] == []


INDEX_URL = "https://example.com/blog"
OLD_PATTERN = "^/blog/[^/]+/?$"
NEW_PATTERN = "^/posts/[^/]+/?$"


def test_scrape_site_self_heal_end_to_end(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A scrape site whose index is redesigned away from its pattern self-heals.

    Drives the full cross-process REQ-006 path over real state: register
    (snapshot back-catalog) → a new article surfaces → the index moves under a
    new path so the stored pattern matches nothing (``zero_links``) → ``heal-site``
    re-scrapes under the new pattern, snapshots the live URLs, rewrites config
    last → the next run is clean. heal-site files NO list reminder (the list holds
    only pages; the heal is reported in the run's push summary).
    """
    site = _MockSite(_index_html("/blog/a", "/blog/b"), content_type="text/html")
    monkeypatch.setattr(cli, "build_client", site.client)

    # --- register the scrape site: snapshots /blog/a and /blog/b seen ---------
    assert (
        cli.main(
            [
                "add-site",
                "--id",
                "blg",
                "--name",
                "Blog",
                "--index-url",
                INDEX_URL,
                "--article-url-pattern",
                OLD_PATTERN,
            ]
        )
        == 0
    )
    assert _out(capsys) == {"site_id": "blg", "kind": "scrape", "snapshotted": 2}
    assert _seen("https://example.com/blog/a") and _seen("https://example.com/blog/b")

    # --- a new article appears under the live pattern -------------------------
    site.body = _index_html("/blog/a", "/blog/b", "/blog/c")
    assert cli.main(["new-entries"]) == 0
    gathered = _out(capsys)
    entry = gathered["entries"]
    assert [e["url"] for e in entry] == ["https://example.com/blog/c"]
    assert entry[0]["kind"] == "scrape"
    assert entry[0]["title"] is None and entry[0]["summary"] is None  # no feed metadata
    assert gathered["sites"] == [{"site_id": "blg", "zero_links": False, "error": None}]

    # --- the site is redesigned: links move to /posts, the pattern matches 0 --
    site.body = _index_html("/posts/a", "/posts/b", "/posts/c")
    assert cli.main(["new-entries"]) == 0
    broken = _out(capsys)
    assert broken["entries"] == []  # nothing matches the stale pattern
    # zero_links fires on a broken pattern (index_matches == 0), NOT a quiet day
    assert broken["sites"] == [{"site_id": "blg", "zero_links": True, "error": None}]

    # --- heal: re-scrape under the new pattern, snapshot, rewrite config (no list reminder)
    assert cli.main(["heal-site", "--site-id", "blg", "--pattern", NEW_PATTERN]) == 0
    healed = _out(capsys)
    assert healed["pattern"] == NEW_PATTERN
    assert healed["snapshotted"] == 3  # the three live /posts URLs
    assert load_sites(sites_path())[0].article_url_pattern == NEW_PATTERN  # config rewritten
    assert _seen("https://example.com/posts/a")  # newly-matched URLs snapshotted (flood guard)

    # --- next run is clean: pattern matches, all snapshotted, no false zero_links
    assert cli.main(["new-entries"]) == 0
    after = _out(capsys)
    assert after["entries"] == []
    assert after["sites"] == [{"site_id": "blg", "zero_links": False, "error": None}]


# --- opt-in browser path end-to-end ---------------------------------------

JS_FEED_URL = "https://js.example.com/feed.xml"
JS_INDEX_URL = "https://js.example.com/blog"
X = ("X", "https://js.example.com/x")
Y = ("Y", "https://js.example.com/y")
Z = ("Z", "https://js.example.com/z")


@pytest.fixture
def browser_env() -> Iterator[None]:
    """Cold browser singleton for the browser-path tests (reset before and after)."""
    browser._bundle = None
    yield
    browser._bundle = None


def test_add_site_requires_browser_snapshots_via_browser(
    state_dir: Path,
    browser_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A JS feed registers through the browser path: its back-catalog is snapshotted
    via the fake Chromium (REQ-007), the flag persists, and the httpx client is
    never touched."""
    # An httpx client that errors if used — the browser add-site must not fetch over it.
    monkeypatch.setattr(cli, "build_client", _MockSite(b"nope", content_type="text/plain").client)
    ctx = FakeContext(response=FakeResponse(status=200, url=JS_FEED_URL, body=_rss(X, Y)))
    install_fake_playwright(monkeypatch, context=ctx)

    assert (
        cli.main(
            [
                "add-site",
                "--id",
                "js",
                "--name",
                "JS",
                "--feed-url",
                JS_FEED_URL,
                "--requires-browser",
            ]
        )
        == 0
    )
    assert _out(capsys) == {"site_id": "js", "kind": "feed", "snapshotted": 2}
    assert load_sites(sites_path())[0].requires_browser is True
    assert _seen(X[1]) and _seen(Y[1])  # back-catalog snapshotted through the browser


def test_new_entries_mixed_registry_returns_both(
    state_dir: Path,
    browser_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A registry of one httpx feed and one browser feed gathers both transports in
    a single run; each surfaces its own post-registration entry."""
    httpx_site = _MockSite(_rss(A, B))
    monkeypatch.setattr(cli, "build_client", httpx_site.client)
    assert cli.main(["add-site", "--id", "hx", "--name", "HX", "--feed-url", FEED_URL]) == 0
    capsys.readouterr()

    # Register the browser feed; its add-site snapshot runs through the fake (X, Y).
    install_fake_playwright(
        monkeypatch,
        context=FakeContext(response=FakeResponse(status=200, url=JS_FEED_URL, body=_rss(X, Y))),
    )
    assert (
        cli.main(
            [
                "add-site",
                "--id",
                "js",
                "--name",
                "JS",
                "--feed-url",
                JS_FEED_URL,
                "--requires-browser",
            ]
        )
        == 0
    )
    capsys.readouterr()

    # A new entry appears on each transport after registration.
    httpx_site.body = _rss(A, B, C)
    install_fake_playwright(
        monkeypatch,
        context=FakeContext(response=FakeResponse(status=200, url=JS_FEED_URL, body=_rss(X, Y, Z))),
    )

    assert cli.main(["new-entries"]) == 0
    out = _out(capsys)
    assert {e["url"] for e in out["entries"]} == {C[1], Z[1]}  # one new from each transport
    statuses = {s["site_id"]: s for s in out["sites"]}
    assert statuses["hx"]["error"] is None
    assert statuses["js"]["error"] is None


def test_heal_site_browser_scrape_succeeds(
    state_dir: Path,
    browser_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """heal-site re-scrapes a browser scrape site THROUGH the browser: replace()
    preserves requires_browser, the new pattern's URLs are snapshotted via the fake,
    config is rewritten last, and the flag survives (the success counterpart to the
    error-path teardown test in test_cli.py)."""
    monkeypatch.setattr(cli, "build_client", _MockSite(b"nope", content_type="text/plain").client)

    # Register the browser scrape site under the OLD pattern; the fake serves /old/*.
    install_fake_playwright(
        monkeypatch,
        context=FakeContext(
            html=_index_html("/old/a", "/old/b").decode(),
            response=FakeResponse(status=200, url=JS_INDEX_URL),
        ),
    )
    assert (
        cli.main(
            [
                "add-site",
                "--id",
                "bs",
                "--name",
                "BS",
                "--index-url",
                JS_INDEX_URL,
                "--article-url-pattern",
                "^/old/[^/]+/?$",
                "--requires-browser",
            ]
        )
        == 0
    )
    capsys.readouterr()

    # The index is redesigned to /posts/*; heal under the new pattern via the browser.
    install_fake_playwright(
        monkeypatch,
        context=FakeContext(
            html=_index_html("/posts/a", "/posts/b", "/posts/c").decode(),
            response=FakeResponse(status=200, url=JS_INDEX_URL),
        ),
    )
    assert cli.main(["heal-site", "--site-id", "bs", "--pattern", "^/posts/[^/]+/?$"]) == 0
    healed = _out(capsys)
    assert healed["pattern"] == "^/posts/[^/]+/?$"
    assert healed["snapshotted"] == 3  # the three live /posts URLs, re-scraped via browser

    site = load_sites(sites_path())[0]
    assert site.article_url_pattern == "^/posts/[^/]+/?$"  # config rewritten last
    assert site.requires_browser is True  # replace() preserved the flag through the heal
    assert _seen("https://js.example.com/posts/a")  # newly-matched URLs snapshotted
