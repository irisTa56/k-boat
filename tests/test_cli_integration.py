"""End-to-end CLI smoke over real state files (TASK-038 / TASK-024).

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
- Forum: ``forum-remind`` / ``forum-mark-seen`` record posts, ``forum-poll-done``
  finalizes per-topic; re-running ``forum-new`` after partial disposition still
  finds the already-seen posts and produces no duplicates (FRM-CON-005).
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
from feed_filter.forum_store import is_post_seen, op_interest_kept
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


# =============================================================================
# Forum end-to-end (TASK-024 / TEST-005)
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
_TOP_DAILY_RSS = b'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>top</title></channel></rss>'
_TOP_WEEKLY_RSS = _TOP_DAILY_RSS


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
    Asserts the two dedupe axes are written independently (FRM-006), the OP is
    reminded once per axis (FRM-005), and 'Filtered Forums' is the list target.
    """
    monkeypatch.setattr(cli, "build_client", _forum_transport)

    rem_calls: list[list[str]] = []

    def fake_runner(argv: list[str], **_: Any) -> SimpleNamespace:
        rem_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "FR-1"}), stderr="")

    # Inject the forum-aware rem runner (accepts list_name kwarg).
    monkeypatch.setattr(
        cli,
        "add_reminder",
        lambda title, url, notes, *, list_name, **kw: reminders_mod.add_reminder(
            title, url, notes, list_name=list_name, runner=fake_runner
        ),
    )

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

    # --- Step 3: Rule-A keep of the OP — `--is-op`, NO `--post-id` ------------
    # Rule A holds no OP post_id (it reads RSS, FRM-002); it records only the
    # topic-grain interest verdict and never touches the post-grain seen store.
    rc = cli.main(
        [
            "forum-remind",
            "--site-id",
            _FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--url",
            f"{_FORUM_URL}/t/my-topic/1234",
            "--title",
            "My Forum Topic",
            "--notes",
            "Cross-domain interesting",
            "--is-op",
        ]
    )
    assert rc == 0

    # rem was called with --list "Filtered Forums", not "Filtered Feeds".
    assert len(rem_calls) == 1
    argv = rem_calls[0]
    list_idx = argv.index("--list")
    assert argv[list_idx + 1] == "Filtered Forums"

    with contextlib.closing(open_db(db_path())) as conn:
        assert op_interest_kept(conn, _FORUM_SITE_ID, 1234) == 1  # Rule-A kept
        # Rule A wrote no post-grain seen: the OP stays re-judgeable by Rule B.
        assert not is_post_seen(conn, _FORUM_SITE_ID, 5001)

    # --- Step 4: Rule-B keep of the same OP (trigger post 5001) ---------------
    # The OP is also a Rule-B trigger (10 likes ≥ 6).  Independent axis: pass
    # `--post-id`, NO `--is-op`.  This records the post-grain seen (FRM-006), so
    # the same OP is reminded once per axis — two items, same topic (FRM-005).
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
            f"{_FORUM_URL}/t/my-topic/1234",
            "--title",
            "My Forum Topic",
            "--notes",
            "Popular and worth reading",
        ]
    )
    assert rc == 0
    assert len(rem_calls) == 2  # one item per axis (FRM-005 / FRM-006)

    with contextlib.closing(open_db(db_path())) as conn:
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
    already-seen.  No duplicate remind; after poll-done the topic advances cleanly
    (FRM-CON-005).
    """
    monkeypatch.setattr(cli, "build_client", _forum_transport)

    rem_calls: list[list[str]] = []

    def fake_runner(argv: list[str], **_: Any) -> SimpleNamespace:
        rem_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "FR-1"}), stderr="")

    monkeypatch.setattr(
        cli,
        "add_reminder",
        lambda title, url, notes, *, list_name, **kw: reminders_mod.add_reminder(
            title, url, notes, list_name=list_name, runner=fake_runner
        ),
    )

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
            "--notes",
            "first run",
        ]
    )
    _out(capsys)
    assert len(rem_calls) == 1

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

    # No new rem call from run 2 (no new candidates to remind).
    assert len(rem_calls) == 1  # still just one from run 1

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
    """The headline FRM-006 behaviour: an OP dropped under Rule A is NOT marked
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
    # because the Rule-A drop did not record it seen at post grain (FRM-006).
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
    """Finding #8 (FRM-CON-001): forum sites never enter new-entries; article sites
    never enter forum-new.  Verified end-to-end with real transport stubs.
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
