"""End-to-end CLI smoke over real state files.

Exercises the actual pipeline — fetch → parse → seen-store → round-robin/cap —
not the monkeypatched core fns of ``test_cli.py``. The only fake is the network
(an ``httpx.MockTransport`` serving a controllable RSS body); the seen-store,
``sites.toml``, and the output vault are real tmp files via the ``state_dir``
fixture, so the cross-process invariants hold against persisted state:

- ``add-site`` snapshots the back-catalog seen *before* writing config,
  so ``new-entries`` returns only entries that appeared *after* registration;
- ``remind`` writes the ``Feeds/`` note *and* records seen in one process,
  so a second ``new-entries`` no longer yields the reminded entry — no duplicate.
- Forum: ``forum-remind`` / ``forum-mark-seen`` record posts, ``forum-poll-done``
  finalizes per-topic; re-running ``forum-new`` after partial disposition still
  finds the already-seen posts and produces no duplicates.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from _fake_playwright import FakeContext, FakeResponse, install_fake_playwright

from feed_filter import browser, cli, forum_store
from feed_filter.config import db_path, sites_path, vault_path
from feed_filter.forum_store import is_post_seen, op_interest_kept
from feed_filter.seen import is_seen, open_db
from feed_filter.sites import load_sites
from kboat.canonical import canonical_url
from kboat.naming import url_slug


def _feeds_dir() -> Path:
    return vault_path() / "Feeds"


def _feed_note_written(url: str) -> bool:
    return (_feeds_dir() / f"{url_slug(str(canonical_url(url)))}.md").is_file()


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
    assert gathered["sites"] == [
        {
            "site_id": "ex",
            "zero_links": False,
            "error": None,
            "unexpected_error": False,
            "consecutive_failures": 0,
            "persistent": False,
        }
    ]

    # --- remind C: writes the Feeds note AND records it seen, atomically ------
    assert (
        cli.main(["remind", "--site-id", "ex", "--url", C[1], "--title", "C", "--summary", "note"])
        == 0
    )
    reminded = _out(capsys)
    assert reminded["kept"] is True
    assert reminded["status"] == "created"
    assert _feed_note_written(C[1])  # the Feeds/ note landed
    assert _seen(C[1])  # recorded in the same process as the write

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

    Drives the full cross-process self-heal path over real state: register
    (snapshot back-catalog) → a new article surfaces → the index moves under a
    new path so the stored pattern matches nothing (``zero_links``) → ``heal-site``
    re-scrapes under the new pattern, snapshots the live URLs, rewrites config
    last → the next run is clean. heal-site writes NO feed note (feed notes are
    pages only; the heal is reported in the run's summary).
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
    assert gathered["sites"] == [
        {
            "site_id": "blg",
            "zero_links": False,
            "error": None,
            "unexpected_error": False,
            "consecutive_failures": 0,
            "persistent": False,
        }
    ]

    # --- the site is redesigned: links move to /posts, the pattern matches 0 --
    site.body = _index_html("/posts/a", "/posts/b", "/posts/c")
    assert cli.main(["new-entries"]) == 0
    broken = _out(capsys)
    assert broken["entries"] == []  # nothing matches the stale pattern
    # zero_links fires on a broken pattern (index_matches == 0), NOT a quiet day.
    # A zero_links scrape is not an outage (error is None), so it must NOT increment
    # the site-health counter — persistence tracks unreachability, not broken patterns.
    assert broken["sites"] == [
        {
            "site_id": "blg",
            "zero_links": True,
            "error": None,
            "unexpected_error": False,
            "consecutive_failures": 0,
            "persistent": False,
        }
    ]

    # --- heal: re-scrape under the new pattern, snapshot, rewrite config (no feed note)
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
    assert after["sites"] == [
        {
            "site_id": "blg",
            "zero_links": False,
            "error": None,
            "unexpected_error": False,
            "consecutive_failures": 0,
            "persistent": False,
        }
    ]


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
    via the fake Chromium, the flag persists, and the httpx client is
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


# =============================================================================
# Forum end-to-end
# =============================================================================

# Hand-authored minimal Discourse fixtures for the integration tests.
# topic 1234: slug "my-topic", like_count=15, two posts (OP post_id=5001 with
# 10 likes, reply post_id=5002 with 0 likes).  Threshold=6 → OP qualifies.

_FORUM_URL = "https://forum.example.com"
_FORUM_SITE_ID = "intf"

_DISCOURSE_TOPIC_JSON = json.dumps(
    {
        "id": 1234,
        "slug": "my-topic",
        "title": "My Forum Topic",
        "like_count": 15,
        "post_stream": {
            "posts": [
                {
                    "id": 5001,
                    "post_number": 1,
                    "cooked": "<p>Hello world, this is the OP.</p>",
                    "actions_summary": [{"id": 2, "count": 10}],
                },
                {
                    "id": 5002,
                    "post_number": 2,
                    "cooked": "<p>A reply with no likes.</p>",
                    "actions_summary": [],
                },
            ]
        },
    }
).encode()


def _discourse_rss(topic_id: int, slug: str, title: str, description: str) -> bytes:
    """Build a minimal Discourse-style RSS 2.0 body with one topic entry."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>Forum</title>'
        f"<item><title>{title}</title>"
        f"<link>{_FORUM_URL}/t/{slug}/{topic_id}</link>"
        f"<description>{description}</description>"
        f"<pubDate>Sun, 14 Jun 2026 10:00:00 GMT</pubDate></item>"
        "</channel></rss>"
    )
    return body.encode("utf-8")


_LATEST_RSS = _discourse_rss(1234, "my-topic", "My Forum Topic", "OP text summary")
# Topic 1234 also appears in the daily top feed so it is poll-eligible:
# only top-feed topics are JSON-polled for Rule B, so the end-to-end Rule-B path
# requires 1234 in a top feed, not latest.rss alone.
_TOP_DAILY_RSS = _discourse_rss(1234, "my-topic", "My Forum Topic", "OP text summary")
_TOP_WEEKLY_RSS = b'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>top</title></channel></rss>'


def _forum_transport() -> httpx.Client:
    """MockTransport that routes forum requests by path and query string."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # httpx stores the raw query as bytes; decode for string matching.
        query = (
            request.url.query.decode("utf-8")
            if isinstance(request.url.query, bytes)
            else str(request.url.query)
        )

        if path == "/latest.rss":
            return httpx.Response(
                200, content=_LATEST_RSS, headers={"content-type": "application/rss+xml"}
            )
        if path == "/top.rss" and "daily" in query:
            return httpx.Response(
                200,
                content=_TOP_DAILY_RSS,
                headers={"content-type": "application/rss+xml"},
            )
        if path == "/top.rss" and "weekly" in query:
            return httpx.Response(
                200,
                content=_TOP_WEEKLY_RSS,
                headers={"content-type": "application/rss+xml"},
            )
        if path.startswith("/t/") and path.endswith(".json"):
            return httpx.Response(
                200, content=_DISCOURSE_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_forum_end_to_end(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full forum run: add-forum → forum-new → Rule-A keep of the OP (verdict
    only) + Rule-B keep of the same OP (post-grain seen) → forum-poll-done.
    Asserts the two dedupe axes are written independently, and that the same
    topic taken up under both rules resolves to one hash-named Feeds note (the
    second keep upserts it) — no duplicate, both axes recorded.
    """
    monkeypatch.setattr(cli, "build_client", _forum_transport)

    # --- Step 1: register the forum site (config only, no snapshot) -----------
    rc = cli.main(
        [
            "add-forum",
            "--id",
            _FORUM_SITE_ID,
            "--name",
            "Test Forum",
            "--forum-url",
            _FORUM_URL,
            "--poll-offsets-days",
            "0",
            "7",
        ]
    )
    assert rc == 0
    _out(capsys)

    # --- Step 2: forum-new → topic 1234 admitted, Rule-A candidate emitted ----
    rc = cli.main(["forum-new", "--site-id", _FORUM_SITE_ID])
    assert rc == 0
    new_out = _out(capsys)

    topics = new_out["topics"]
    # Rule-A candidate for topic 1234 should appear (op_interest_kept=NULL).
    rule_a_topics = [t for t in topics if t["rule"] == "A"]
    assert any(t["topic_id"] == 1234 for t in rule_a_topics)

    # Rule-B: topic 1234 is due (offset 0 elapsed) and post 5001 qualifies (10 likes ≥ 6).
    rule_b_topics = [t for t in topics if t["rule"] == "B"]
    assert any(t["topic_id"] == 1234 for t in rule_b_topics)

    # Polls worklist contains topic 1234.
    polls = new_out["polls"]
    assert any(p["topic_id"] == 1234 for p in polls)
    poll = next(p for p in polls if p["topic_id"] == 1234)

    # discourse_fetches: 3 RSS feeds + one topic-JSON for the single due topic 1234.
    assert new_out["discourse_fetches"] == 4

    topic_url = f"{_FORUM_URL}/t/my-topic/1234"

    # --- Step 3: Rule-A keep of the OP — `--is-op`, NO `--post-id` ------------
    # Rule A holds no OP post_id (it reads RSS); it records only the
    # topic-grain interest verdict and never touches the post-grain seen store.
    rc = cli.main(
        [
            "forum-remind",
            "--site-id",
            _FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--url",
            topic_url,
            "--title",
            "My Forum Topic",
            "--summary",
            "Cross-domain",
            "--is-op",
        ]
    )
    assert rc == 0
    a_out = _out(capsys)
    assert a_out["status"] == "created"  # first write for this topic → the note is created
    assert _feed_note_written(topic_url)

    with contextlib.closing(open_db(db_path())) as conn:
        assert op_interest_kept(conn, _FORUM_SITE_ID, 1234) == 1  # Rule-A kept
        # Rule A wrote no post-grain seen: the OP stays re-judgeable by Rule B.
        assert not is_post_seen(conn, _FORUM_SITE_ID, 5001)

    # --- Step 4: Rule-B keep of the same OP (trigger post 5001) ---------------
    # The OP is also a Rule-B trigger (10 likes ≥ 6).  Independent axis: pass
    # `--post-id`, NO `--is-op`.  The topic URL already has a note (Step 3), so
    # this keep UPSERTS that same hash-named note — no duplicate — while still
    # recording the post-grain seen: both axes written.
    rc = cli.main(
        [
            "forum-remind",
            "--site-id",
            _FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--post-id",
            "5001",
            "--url",
            topic_url,
            "--title",
            "My Forum Topic",
            "--summary",
            "Popular",
        ]
    )
    assert rc == 0
    b_out = _out(capsys)
    assert b_out["status"] == "updated"  # same URL → same note, upserted (not a duplicate)
    # Exactly one Feeds note for this topic across both keeps.
    assert [n.name for n in _feeds_dir().glob("*.md")] == [
        f"{url_slug(str(canonical_url(topic_url)))}.md"
    ]

    with contextlib.closing(open_db(db_path())) as conn:
        # Post-grain seen IS recorded on the Rule-B keep.
        assert is_post_seen(conn, _FORUM_SITE_ID, 5001)

    # --- Step 5: forum-poll-done (last call for topic 1234) -------------------
    rc = cli.main(
        [
            "forum-poll-done",
            "--site-id",
            _FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--like-count",
            str(poll["like_count"]),
        ]
    )
    assert rc == 0

    with contextlib.closing(open_db(db_path())) as conn:
        row = conn.execute(
            "SELECT completed_polls, last_like_count, retired FROM forum_watch "
            "WHERE site_id = ? AND topic_id = ?",
            (_FORUM_SITE_ID, 1234),
        ).fetchone()
    assert row[0] == 1  # completed_polls advanced
    assert row[1] == poll["like_count"]  # like_count stored
    # Not retired yet (has offset 7 remaining).
    assert row[2] == 0


def test_forum_never_lost_ordering_reruns_find_seen_posts(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Never-lost ordering: a run that disposes posts but is interrupted before
    forum-poll-done can re-run forum-new and re-derive the same posts as
    already-seen.  No duplicate remind; after poll-done the topic advances cleanly.
    """
    monkeypatch.setattr(cli, "build_client", _forum_transport)

    # Register the forum site.
    rc = cli.main(
        [
            "add-forum",
            "--id",
            _FORUM_SITE_ID,
            "--name",
            "Test Forum",
            "--forum-url",
            _FORUM_URL,
            "--poll-offsets-days",
            "0",
        ]
    )
    assert rc == 0
    _out(capsys)

    # --- Run 1: forum-new → remind post 5001 → CRASH (poll-done never called) -
    cli.main(["forum-new", "--site-id", _FORUM_SITE_ID])
    _out(capsys)

    # Disposition the OP as a Rule-B trigger (post-grain seen; no --is-op).
    cli.main(
        [
            "forum-remind",
            "--site-id",
            _FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--post-id",
            "5001",
            "--url",
            f"{_FORUM_URL}/t/my-topic/1234",
            "--title",
            "My Forum Topic",
            "--summary",
            "first",
        ]
    )
    _out(capsys)
    with contextlib.closing(open_db(db_path())) as conn:
        assert is_post_seen(conn, _FORUM_SITE_ID, 5001)  # recorded seen before the crash

    # poll-done is NOT called (simulated crash).
    with contextlib.closing(open_db(db_path())) as conn:
        row = conn.execute(
            "SELECT completed_polls FROM forum_watch WHERE site_id = ? AND topic_id = ?",
            (_FORUM_SITE_ID, 1234),
        ).fetchone()
    assert row[0] == 0  # poll not advanced

    # --- Run 2: forum-new again — post 5001 is already seen, no new Rule-B candidate ----
    cli.main(["forum-new", "--site-id", _FORUM_SITE_ID])
    new2 = _out(capsys)

    # Post 5001 is already seen, so Rule-B produces no trigger_posts for it.
    rule_b_topics = [t for t in new2["topics"] if t["rule"] == "B"]
    # If a Rule-B candidate exists for topic 1234, it must not include post 5001.
    for t in rule_b_topics:
        if t["topic_id"] == 1234:
            assert not any(p["post_id"] == 5001 for p in t["trigger_posts"]), (
                "post 5001 is already seen; it must not appear as a trigger post again"
            )

    # --- After re-run, complete the cycle with forum-poll-done ---------------
    polls2 = new2["polls"]
    if polls2:
        poll = polls2[0]
        cli.main(
            [
                "forum-poll-done",
                "--site-id",
                _FORUM_SITE_ID,
                "--topic-id",
                str(poll["topic_id"]),
                "--like-count",
                str(poll["like_count"]),
            ]
        )
        _out(capsys)

        with contextlib.closing(open_db(db_path())) as conn:
            row = conn.execute(
                "SELECT completed_polls, retired FROM forum_watch "
                "WHERE site_id = ? AND topic_id = ?",
                (_FORUM_SITE_ID, 1234),
            ).fetchone()
        # With poll_offsets_days=(0,), one poll retires the topic.
        assert row[0] == 1
        assert row[1] == 1  # retired


def test_forum_rule_a_drop_resurfaces_under_rule_b(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The headline behaviour: an OP dropped under Rule A is NOT marked
    post-grain seen (only the topic-grain verdict is set), so it is still
    re-judged under Rule B if it crosses the like bar.  This is the mirror of
    ``test_forum_never_lost_ordering_reruns_find_seen_posts`` (where a post-grain
    *seen* post does NOT resurface).
    """
    monkeypatch.setattr(cli, "build_client", _forum_transport)

    # Register with offsets=(0,) so the topic stays due each run until poll-done.
    rc = cli.main(
        [
            "add-forum",
            "--id",
            _FORUM_SITE_ID,
            "--name",
            "Test Forum",
            "--forum-url",
            _FORUM_URL,
            "--poll-offsets-days",
            "0",
        ]
    )
    assert rc == 0
    _out(capsys)

    # Run 1: forum-new admits topic 1234 and emits its Rule-A candidate.
    cli.main(["forum-new", "--site-id", _FORUM_SITE_ID])
    _out(capsys)

    # Drop the OP under Rule A: --is-op, NO --post-id (verdict only).
    rc = cli.main(
        [
            "forum-mark-seen",
            "--site-id",
            _FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--url",
            f"{_FORUM_URL}/t/my-topic/1234",
            "--title",
            "My Forum Topic",
            "--is-op",
        ]
    )
    assert rc == 0
    _out(capsys)  # drain the mark-seen output

    with contextlib.closing(open_db(db_path())) as conn:
        assert op_interest_kept(conn, _FORUM_SITE_ID, 1234) == 0  # Rule-A dropped
        # The OP was NOT marked post-grain seen — that is what lets Rule B reopen it.
        assert not is_post_seen(conn, _FORUM_SITE_ID, 5001)

    # Run 2: the OP (post 5001, 10 likes ≥ threshold) still surfaces under Rule B,
    # because the Rule-A drop did not record it seen at post grain.
    cli.main(["forum-new", "--site-id", _FORUM_SITE_ID])
    new2 = _out(capsys)
    rule_b_for_topic = [t for t in new2["topics"] if t["rule"] == "B" and t["topic_id"] == 1234]
    assert rule_b_for_topic, "a dropped-by-A OP must remain eligible for Rule B"
    assert any(p["post_id"] == 5001 for t in rule_b_for_topic for p in t["trigger_posts"]), (
        "the A-dropped OP (post 5001) must resurface as a Rule-B trigger"
    )


def test_forum_site_excluded_from_new_entries_and_vice_versa(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Forum sites never enter new-entries; article sites never enter forum-new.

    Verified end-to-end with real transport stubs.
    """
    # Article site mock.
    article_site = _MockSite(_rss(A, B))
    monkeypatch.setattr(cli, "build_client", article_site.client)

    # Register both kinds.
    assert cli.main(["add-site", "--id", "art", "--name", "Art", "--feed-url", FEED_URL]) == 0
    _out(capsys)
    # Forum site registration doesn't need a transport.
    assert (
        cli.main(["add-forum", "--id", "foru", "--name", "Forum", "--forum-url", _FORUM_URL]) == 0
    )
    _out(capsys)

    # new-entries: only the article site should be gathered.
    # Swap to the article client so the feed can be fetched.
    article_site.body = _rss(A, B, C)
    rc = cli.main(["new-entries"])
    assert rc == 0
    ne_out = _out(capsys)
    site_ids = {s["site_id"] for s in ne_out["sites"]}
    assert "art" in site_ids
    assert "foru" not in site_ids

    # forum-new: only the forum site should be gathered.
    # We need a transport that answers forum requests.
    monkeypatch.setattr(cli, "build_client", _forum_transport)
    rc = cli.main(["forum-new"])
    assert rc == 0
    fn_out = _out(capsys)
    forum_site_ids = {s["site_id"] for s in fn_out["sites"]}
    assert "foru" in forum_site_ids
    assert "art" not in forum_site_ids


# ---------------------------------------------------------------------------
# Site-health escalation: forum-new increments the durable
# consecutive-failure counter on whole-site unreachability, resets on a
# reachable run, and never increments on a dead-topic retirement alone.
# ---------------------------------------------------------------------------


def _all_feeds_fail_transport() -> httpx.Client:
    """Every request fails 503 — every discovery feed is unreachable."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _dead_topic_transport() -> httpx.Client:
    """Discovery feeds succeed (site reachable) but the due topic's JSON 404s.

    The site is reachable (all_feeds_failed is False), yet gather_forum emits a
    benign ``retiring dead topic`` error for the deleted topic — the exact case
    that must NOT increment the failure counter.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = (
            request.url.query.decode("utf-8")
            if isinstance(request.url.query, bytes)
            else str(request.url.query)
        )
        if path == "/latest.rss":
            return httpx.Response(
                200, content=_LATEST_RSS, headers={"content-type": "application/rss+xml"}
            )
        if path == "/top.rss" and "daily" in query:
            return httpx.Response(
                200, content=_TOP_DAILY_RSS, headers={"content-type": "application/rss+xml"}
            )
        if path == "/top.rss" and "weekly" in query:
            return httpx.Response(
                200, content=_TOP_WEEKLY_RSS, headers={"content-type": "application/rss+xml"}
            )
        if path.startswith("/t/") and path.endswith(".json"):
            return httpx.Response(404, text="gone")  # deleted topic → permanent retirement
        return httpx.Response(404, text="not found")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _register_forum(offsets: tuple[str, ...] = ("0", "7")) -> None:
    """Register the integration forum site (config only, no snapshot)."""
    rc = cli.main(
        [
            "add-forum",
            "--id",
            _FORUM_SITE_ID,
            "--name",
            "Test Forum",
            "--forum-url",
            _FORUM_URL,
            "--poll-offsets-days",
            *offsets,
        ]
    )
    assert rc == 0


def _forum_status(out: dict[str, Any]) -> dict[str, Any]:
    """The single forum site's status entry from a forum-new output."""
    return next(s for s in out["sites"] if s["site_id"] == _FORUM_SITE_ID)


def test_forum_new_escalates_persistent_then_resets(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A wholly-unreachable site increments the durable counter each run and flips
    ``persistent`` at the threshold (3); the first reachable run resets it to 0.

    This is the cross-run signal the whole feature exists for: stateless runs that
    would each re-derive "transient" now share one durable count.
    """
    _register_forum()
    capsys.readouterr()  # discard add-forum output

    # Three consecutive wholly-unreachable runs → count 1, 2, 3; persistent at 3.
    monkeypatch.setattr(cli, "build_client", _all_feeds_fail_transport)
    for expected_count in (1, 2, 3):
        assert cli.main(["forum-new", "--site-id", _FORUM_SITE_ID]) == 0
        status = _forum_status(_out(capsys))
        assert status["error"] is not None, "an unreachable site must report an error"
        assert status["consecutive_failures"] == expected_count
        assert status["persistent"] is (expected_count >= 3)

    # A reachable run resets the streak: count back to 0, persistent cleared.
    monkeypatch.setattr(cli, "build_client", _forum_transport)
    assert cli.main(["forum-new", "--site-id", _FORUM_SITE_ID]) == 0
    status = _forum_status(_out(capsys))
    assert status["error"] is None
    assert status["consecutive_failures"] == 0
    assert status["persistent"] is False


def test_forum_new_dead_topic_retirement_does_not_increment(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reachable run whose only error is a dead-topic retirement must not count
    as a site failure — the counter keys on whole-site
    unreachability, not on the combined error string.
    """
    _register_forum(offsets=("0",))  # offset 0 → topic 1234 is due the run it is admitted
    capsys.readouterr()

    monkeypatch.setattr(cli, "build_client", _dead_topic_transport)
    assert cli.main(["forum-new", "--site-id", _FORUM_SITE_ID]) == 0
    status = _forum_status(_out(capsys))

    # The gather surfaced the benign retirement error, but the site was reachable.
    assert status["error"] is not None
    assert "retiring dead topic" in status["error"]
    assert status["consecutive_failures"] == 0, "a dead-topic retirement is not a site failure"
    assert status["persistent"] is False


# ---------------------------------------------------------------------------
# Per-topic failure isolation: a topic whose gather did not complete stays out
# of the emitted polls worklist and re-polls on the next run.
# ---------------------------------------------------------------------------


def _multi_topic_rss(*topic_ids: int) -> bytes:
    """A Discourse-style RSS body carrying one item per topic id."""
    items = "".join(
        f"<item><title>Topic {tid}</title>"
        f"<link>{_FORUM_URL}/t/topic-{tid}/{tid}</link>"
        f"<description>OP text summary</description>"
        f"<pubDate>Sun, 14 Jun 2026 10:00:00 GMT</pubDate></item>"
        for tid in topic_ids
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>Forum</title>{items}</channel></rss>'
    )
    return body.encode("utf-8")


def _one_post_topic_json(topic_id: int, post_id: int) -> bytes:
    """A topic payload with a single qualifying post, id given by the caller.

    Distinct post ids per topic are what let a fault be injected for one topic's
    per-post scan and not the other's.
    """
    return json.dumps(
        {
            "id": topic_id,
            "slug": f"topic-{topic_id}",
            "title": f"Topic {topic_id}",
            "like_count": 15,
            "post_stream": {
                "posts": [
                    {
                        "id": post_id,
                        "post_number": 1,
                        "cooked": "<p>Body.</p>",
                        "actions_summary": [{"id": 2, "count": 10}],
                    }
                ]
            },
        }
    ).encode()


def _two_topic_transport() -> httpx.Client:
    """Feeds surfacing topics 1234 and 1235, each with its own topic JSON."""
    feed = _multi_topic_rss(1234, 1235)
    payloads = {
        "/t/1234.json": _one_post_topic_json(1234, 5001),
        "/t/1235.json": _one_post_topic_json(1235, 6001),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in ("/latest.rss", "/top.rss"):
            return httpx.Response(
                200, content=feed, headers={"content-type": "application/rss+xml"}
            )
        if path in payloads:
            return httpx.Response(
                200, content=payloads[path], headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_forum_new_topic_that_never_completed_stays_out_of_polls(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A topic whose gather raised is withheld from ``polls`` and re-polls next run.

    This is the load-bearing property of per-topic isolation: ``polls`` is what
    the run skill feeds to ``forum-poll-done``, so a topic that reached it without
    completing would have its poll advanced past posts nobody dispositioned. The
    other due topic must still emit — a partially-gathered site emits the topics
    it finished.
    """
    _register_forum(offsets=("0", "7"))
    capsys.readouterr()  # discard add-forum output
    monkeypatch.setattr(cli, "build_client", _two_topic_transport)

    # Fault injection: topic 1235's per-post seen check raises, after that topic's
    # poll record was already assembled.
    failing_posts = {6001}
    real_is_post_seen = forum_store.is_post_seen

    def flaky_is_post_seen(conn: sqlite3.Connection, site_id: str, post_id: int) -> bool:
        if post_id in failing_posts:
            raise RuntimeError("seen lookup blew up")
        return real_is_post_seen(conn, site_id, post_id)

    monkeypatch.setattr(forum_store, "is_post_seen", flaky_is_post_seen)

    assert cli.main(["forum-new", "--site-id", _FORUM_SITE_ID]) == 0
    out = _out(capsys)

    assert [p["topic_id"] for p in out["polls"]] == [1234], (
        "the topic that raised must not be offered for forum-poll-done"
    )
    # Admission is a separate path, so both topics still have Rule-A candidates;
    # only 1235's Rule-B work was lost.
    assert {t["topic_id"] for t in out["topics"] if t["rule"] == "A"} == {1234, 1235}
    assert [t["topic_id"] for t in out["topics"] if t["rule"] == "B"] == [1234]

    status = _forum_status(out)
    assert status["unexpected_error"] is True
    assert "topic 1235" in status["error"]
    # Reachable site: the Rule-B failure is not a site-health failure run.
    assert status["consecutive_failures"] == 0

    # Nothing was recorded for 1235, so the next run re-polls it and re-derives
    # the same post — never-lost.
    failing_posts.clear()
    assert cli.main(["forum-new", "--site-id", _FORUM_SITE_ID]) == 0
    out2 = _out(capsys)
    assert 1235 in {p["topic_id"] for p in out2["polls"]}
    assert 6001 in {
        p["post_id"]
        for t in out2["topics"]
        if t["rule"] == "B" and t["topic_id"] == 1235
        for p in t["trigger_posts"]
    }
    assert _forum_status(out2)["unexpected_error"] is False


def test_forum_new_persisting_topic_failure_re_surfaces_next_run(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cause that does not clear keeps re-surfacing rather than going quiet.

    This is the mitigation the design rests on: nothing retires a topic whose
    gather never completes, so the flag has to reappear on every run for a human
    to act on. The accepted cost shows here too — the failing topics are still
    due, so the second run pays their topic-JSON calls again instead of settling.
    With a fixed due set that repeats at the same price per run; it *grows* only
    as the admission enrolls further topics, which this fixture's feed does not.
    """
    _register_forum(offsets=("0", "7"))
    capsys.readouterr()  # discard add-forum output
    monkeypatch.setattr(cli, "build_client", _two_topic_transport)

    def always_failing(conn: sqlite3.Connection, site_id: str, post_id: int) -> bool:
        raise RuntimeError("seen lookup blew up")

    monkeypatch.setattr(forum_store, "is_post_seen", always_failing)

    assert cli.main(["forum-new", "--site-id", _FORUM_SITE_ID]) == 0
    first = _out(capsys)
    assert first["polls"] == [], "neither topic completed"

    assert cli.main(["forum-new", "--site-id", _FORUM_SITE_ID]) == 0
    second = _out(capsys)
    assert second["polls"] == []
    # Both topics are still due, so the run pays their topic-JSON calls again
    # rather than dropping them: same cost repeated, never amortized away.
    assert second["discourse_fetches"] == first["discourse_fetches"]
    assert "topic 1234" in _forum_status(second)["error"]
    assert "topic 1235" in _forum_status(second)["error"]


def test_forum_new_admission_raising_mid_loop_re_emits_rule_a_next_run(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A raise partway through the admission loses no Rule-A judgment.

    This is what licenses the per-site boundary absorbing an admission failure:
    ``admit_topic`` writes, but idempotently, and a topic admitted just before the
    raise still has ``op_interest_kept`` NULL — so the next run re-emits its
    Rule-A candidate rather than skipping a topic that was never judged.
    """
    _register_forum(offsets=("0", "7"))
    capsys.readouterr()  # discard add-forum output
    monkeypatch.setattr(cli, "build_client", _two_topic_transport)

    # Fault injection: the admission raises on the second topic it admits, after
    # the first has already been written to forum_watch.
    real_admit_topic = forum_store.admit_topic
    admitted: list[int] = []

    def flaky_admit_topic(
        conn: sqlite3.Connection,
        site_id: str,
        topic_id: int,
        *,
        first_seen_at: int,
        poll_eligible: bool,
    ) -> None:
        if admitted:
            raise RuntimeError("admit blew up")
        admitted.append(topic_id)
        real_admit_topic(
            conn,
            site_id,
            topic_id,
            first_seen_at=first_seen_at,
            poll_eligible=poll_eligible,
        )

    monkeypatch.setattr(forum_store, "admit_topic", flaky_admit_topic)

    assert cli.main(["forum-new", "--site-id", _FORUM_SITE_ID]) == 0
    out = _out(capsys)
    # The admission fetched all three feeds before raising, and the gather fetched
    # the one due topic — but a caught call's count never rides home, so the
    # politeness metric reports only the gather's. It is a floor, not a total.
    assert out["discourse_fetches"] == 1
    assert [t for t in out["topics"] if t["rule"] == "A"] == [], (
        "the whole Rule-A pass is forfeited, not partly emitted"
    )
    # The separately-guarded Rule-B gather still ran, over the one topic the
    # admission had written before it raised.
    assert [t["topic_id"] for t in out["topics"] if t["rule"] == "B"] == [1234]
    status = _forum_status(out)
    assert status["unexpected_error"] is True
    assert status["consecutive_failures"] == 1, "no reachability verdict was returned"

    # Next run, with the fault cleared: both topics are judged, including the one
    # already written to forum_watch before the raise.
    monkeypatch.setattr(forum_store, "admit_topic", real_admit_topic)
    assert cli.main(["forum-new", "--site-id", _FORUM_SITE_ID]) == 0
    out2 = _out(capsys)
    assert {t["topic_id"] for t in out2["topics"] if t["rule"] == "A"} == {1234, 1235}
    assert _forum_status(out2)["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# Site-health escalation, article path: new-entries increments the
# durable counter when a site's gather errors, resets on a reachable run, and
# never increments on a zero_links scrape (the last covered by the heal-flow test
# above, which now asserts consecutive_failures == 0 on the zero_links run).
# ---------------------------------------------------------------------------


def _feed_fail_transport() -> httpx.Client:
    """Every request fails 503 — the article feed is unreachable."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_new_entries_escalates_persistent_then_resets(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An article site whose feed keeps erroring increments the durable counter each
    run and flips ``persistent`` at the threshold (3); the first reachable run
    resets it to 0 — the article-path parity of the forum escalation.
    """
    feed = _MockSite(_rss(A, B))
    monkeypatch.setattr(cli, "build_client", feed.client)
    assert cli.main(["add-site", "--id", "ex", "--name", "Example", "--feed-url", FEED_URL]) == 0
    capsys.readouterr()  # discard add-site output

    # Three consecutive failed gathers → count 1, 2, 3; persistent at 3. The error
    # is a hard fetch failure, not a zero_links scrape, so it counts.
    monkeypatch.setattr(cli, "build_client", _feed_fail_transport)
    for expected_count in (1, 2, 3):
        assert cli.main(["new-entries", "--site-id", "ex"]) == 0
        status = next(s for s in _out(capsys)["sites"] if s["site_id"] == "ex")
        assert status["error"] is not None, "a failed gather must report an error"
        assert status["zero_links"] is False
        assert status["consecutive_failures"] == expected_count
        assert status["persistent"] is (expected_count >= 3)

    # A reachable run resets the streak: count back to 0, persistent cleared.
    feed.body = _rss(A, B, C)
    monkeypatch.setattr(cli, "build_client", feed.client)
    assert cli.main(["new-entries", "--site-id", "ex"]) == 0
    status = next(s for s in _out(capsys)["sites"] if s["site_id"] == "ex")
    assert status["error"] is None
    assert status["consecutive_failures"] == 0
    assert status["persistent"] is False
