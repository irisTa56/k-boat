"""Behavior tests for forum_pipeline.py — admission union and Rule-B candidate assembly.

Network-free via ``httpx.MockTransport`` (mirrors ``test_pipeline.py``); the
seen-store is a real tmp_path SQLite DB via ``seen.open_db`` so the v2 forum
tables exist (mirrors ``test_forum_store.py``).  ``now`` is injected (FRM-PAT-002).

The MockTransport handler routes by URL path to serve the right fixture:
- ``/latest.rss``       → ``discourse_latest.rss``
- ``/top.rss``          → ``discourse_top.rss`` (with rank order ≠ date order)
- ``/t/<id>.json``      → ``discourse_topic.json``
- Any other path        → 500 (used to assert no JSON fetch in Rule-A tests)

TASK-019 coverage:
- Admission union + dedupe across latest / daily / weekly.
- Top-feed rank order preserved through ``sort=False`` (daily/weekly top-N).
- Rule-A candidates emitted from the feed with no JSON fetch.
- Rule-A dedupe: a topic whose ``op_interest_kept`` is already set emits nothing.
- Rule-B: due-topic candidate assembly with effective-threshold switch.
- ``last_like_count`` short-circuit: fires only after first poll.
- Per-site error absorption on feed failure and JSON failure.
- No writes to the DB during ``gather_forum``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from feed_filter import forum_store, seen
from feed_filter.forum_pipeline import (
    RuleACandidate,
    RuleBCandidate,
    admit_from_feeds,
    gather_forum,
)
from feed_filter.sites import SiteConfig

Handler = Callable[[httpx.Request], httpx.Response]

# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"

_LATEST_RSS = (FIXTURES / "discourse_latest.rss").read_bytes()
_TOP_RSS = (FIXTURES / "discourse_top.rss").read_bytes()
_TOPIC_JSON = (FIXTURES / "discourse_topic.json").read_bytes()

# Topic ids from the fixture files (for assertion clarity).
# latest.rss: 101, 102, 103
# top.rss:    201 (rank 1, dated Jun 1), 202 (rank 2, dated Jun 12), 203 (rank 3, dated Jun 14)
# discourse_topic.json: topic id 1234, post ids 5001/5002/5003

FORUM_URL = "https://forum.example.com"
SITE_ID = "test-forum"
NOW = 1_000_000  # injected unix time


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = seen.open_db(tmp_path / "feed-filter.db")
    yield c
    c.close()


def _forum_site(
    *,
    daily_watch_count: int | None = None,
    weekly_watch_count: int | None = None,
    like_threshold: int | None = None,
    interest_like_threshold: int | None = None,
    poll_offsets_days: tuple[int, ...] | None = None,
) -> SiteConfig:
    return SiteConfig(
        id=SITE_ID,
        name="Test Forum",
        forum_url=FORUM_URL,
        daily_watch_count=daily_watch_count,
        weekly_watch_count=weekly_watch_count,
        like_threshold=like_threshold,
        interest_like_threshold=interest_like_threshold,
        poll_offsets_days=poll_offsets_days,
    )


def _client_from_handler(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _standard_handler(request: httpx.Request) -> httpx.Response:
    """Route by path; 500 for unknown paths (catches unexpected JSON fetches)."""
    path = request.url.path
    if path == "/latest.rss":
        return httpx.Response(
            200, content=_LATEST_RSS, headers={"content-type": "application/rss+xml"}
        )
    if path == "/top.rss":
        return httpx.Response(
            200, content=_TOP_RSS, headers={"content-type": "application/rss+xml"}
        )
    if path.startswith("/t/") and path.endswith(".json"):
        return httpx.Response(
            200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
        )
    return httpx.Response(500, text="unexpected")


def _no_json_handler(request: httpx.Request) -> httpx.Response:
    """Like _standard_handler but 500 on any /t/<id>.json request.

    Used to assert Rule-A paths never trigger a JSON fetch (FRM-002).
    """
    path = request.url.path
    if path == "/latest.rss":
        return httpx.Response(
            200, content=_LATEST_RSS, headers={"content-type": "application/rss+xml"}
        )
    if path == "/top.rss":
        return httpx.Response(
            200, content=_TOP_RSS, headers={"content-type": "application/rss+xml"}
        )
    # Any JSON fetch is forbidden in the Rule-A path.
    if path.startswith("/t/") and path.endswith(".json"):
        raise AssertionError(f"Rule-A path must not fetch JSON, but fetched {path}")
    return httpx.Response(500, text="unexpected")


# ---------------------------------------------------------------------------
# admit_from_feeds: admission union and dedupe (TASK-019)
# ---------------------------------------------------------------------------


def test_admission_union_admits_all_three_feeds(conn: sqlite3.Connection) -> None:
    """Topics from latest, daily, and weekly feeds are all admitted (FRM-001)."""
    site = _forum_site(daily_watch_count=3, weekly_watch_count=3)
    with _client_from_handler(_no_json_handler) as client:
        result = admit_from_feeds(conn, site, client=client, now=NOW)

    assert result.error is None
    # latest.rss: 101, 102, 103; top.rss (daily+weekly both use same fixture): 201, 202, 203
    admitted_ids = {c.topic_id for c in result.candidates}
    assert {101, 102, 103, 201, 202, 203} == admitted_ids
    # Three RSS fetches (latest + daily + weekly), all successful (FRM-001).
    assert result.fetch_count == 3


def test_admission_union_dedupes_overlapping_topics(conn: sqlite3.Connection) -> None:
    """A topic id that appears in multiple feeds is admitted once (FRM-001).

    We pre-admit topic 101 (from latest.rss) into forum_watch, then admit the
    union and assert only one row exists for topic 101.
    """
    site = _forum_site(daily_watch_count=3, weekly_watch_count=3)
    # Pre-admit topic 101 so it appears in both latest and already exists.
    forum_store.admit_topic(conn, SITE_ID, 101, first_seen_at=NOW - 100)

    with _client_from_handler(_no_json_handler) as client:
        result = admit_from_feeds(conn, site, client=client, now=NOW)

    # Row count for topic 101 must be exactly 1 (INSERT-OR-IGNORE).
    count = conn.execute(
        "SELECT COUNT(*) FROM forum_watch WHERE site_id = ? AND topic_id = ?",
        (SITE_ID, 101),
    ).fetchone()[0]
    assert count == 1, "duplicate admission must not write a second row"
    assert result.error is None


def test_top_feed_rank_order_preserved(conn: sqlite3.Connection) -> None:
    """Top-N is the N most popular topics, not the N newest (FRM-001 / FRM-GUD-002).

    discourse_top.rss ranks topics 201 > 202 > 203 by popularity, but the
    publication dates are Jun 1 < Jun 12 < Jun 14 (reverse order).  With
    sort=False the daily top-1 must be 201 (rank 1), not 203 (newest).
    """
    # Use daily_count=1 to force top-1 selection.
    site = _forum_site(daily_watch_count=1, weekly_watch_count=0)

    with _client_from_handler(_no_json_handler) as client:
        result = admit_from_feeds(conn, site, client=client, now=NOW)

    admitted_ids = {c.topic_id for c in result.candidates}
    # Latest: 101, 102, 103; daily top-1: 201 (not 203 which is newest).
    assert 201 in admitted_ids, "rank-1 topic must be admitted"
    assert 202 not in admitted_ids, "rank-2 topic must not be admitted under top-1"
    assert 203 not in admitted_ids, "newest topic must not be admitted when not top-1"


def test_rule_a_candidates_emitted_without_json_fetch(conn: sqlite3.Connection) -> None:
    """Rule-A candidates come from RSS entries only — no JSON fetch (FRM-002).

    The _no_json_handler raises AssertionError if any /t/<id>.json path is
    requested, so this test fails if the implementation fetches JSON.
    """
    site = _forum_site()
    with _client_from_handler(_no_json_handler) as client:
        result = admit_from_feeds(conn, site, client=client, now=NOW)

    assert len(result.candidates) > 0, "must emit at least one Rule-A candidate"
    # Each candidate carries data from the RSS feed.
    for cand in result.candidates:
        assert isinstance(cand, RuleACandidate)
        assert cand.title  # entries in the fixture have titles


def test_rule_a_skips_topic_with_op_interest_set(conn: sqlite3.Connection) -> None:
    """A topic whose op_interest_kept is already set emits no Rule-A candidate (FRM-002).

    Rule A is judged once: once the verdict is recorded (kept=1 or kept=0),
    the topic must not appear as a new Rule-A candidate.
    """
    site = _forum_site()
    # Pre-admit topic 101 and record a verdict.
    forum_store.admit_topic(conn, SITE_ID, 101, first_seen_at=NOW)
    forum_store.set_op_verdict(conn, SITE_ID, 101, kept=1)

    with _client_from_handler(_no_json_handler) as client:
        result = admit_from_feeds(conn, site, client=client, now=NOW)

    assert all(c.topic_id != 101 for c in result.candidates), (
        "topic with op_interest_kept set must not appear as a Rule-A candidate"
    )


def test_rule_a_skips_topic_with_op_dropped(conn: sqlite3.Connection) -> None:
    """A topic dropped by Rule A (kept=0) also emits no Rule-A candidate (FRM-002)."""
    site = _forum_site()
    forum_store.admit_topic(conn, SITE_ID, 102, first_seen_at=NOW)
    forum_store.set_op_verdict(conn, SITE_ID, 102, kept=0)

    with _client_from_handler(_no_json_handler) as client:
        result = admit_from_feeds(conn, site, client=client, now=NOW)

    assert all(c.topic_id != 102 for c in result.candidates)


def test_rule_a_candidate_carries_op_text(conn: sqlite3.Connection) -> None:
    """Rule-A candidates include the RSS summary as op_text (FRM-002)."""
    site = _forum_site(daily_watch_count=0, weekly_watch_count=0)
    with _client_from_handler(_no_json_handler) as client:
        result = admit_from_feeds(conn, site, client=client, now=NOW)

    # All three latest topics should have op_text from the fixture descriptions.
    for cand in result.candidates:
        assert cand.op_text is not None, "RSS description must propagate to op_text"
        assert "topic" in cand.op_text.lower() or "OP" in cand.op_text


def test_admit_error_absorption_continues_on_feed_failure(conn: sqlite3.Connection) -> None:
    """A FetchError on one feed is absorbed; the other feeds still produce candidates (FRM-CON-005)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/latest.rss":
            return httpx.Response(500, text="boom")  # latest fails
        if path == "/top.rss":
            return httpx.Response(
                200, content=_TOP_RSS, headers={"content-type": "application/rss+xml"}
            )
        return httpx.Response(404, text="not found")

    site = _forum_site(daily_watch_count=3, weekly_watch_count=3)
    with _client_from_handler(handler) as client:
        result = admit_from_feeds(conn, site, client=client, now=NOW)

    assert result.error is not None, "a failed feed must produce an error message"
    # Top feeds succeeded → daily/weekly topics admitted.
    admitted_ids = {c.topic_id for c in result.candidates}
    assert {201, 202, 203} & admitted_ids, "surviving feed topics must still be admitted"
    # Latest failed → 101/102/103 may not be admitted.
    assert {101, 102, 103}.isdisjoint(admitted_ids), "failed-feed topics must not be admitted"


def test_admit_all_feeds_fail_returns_error_no_candidates(conn: sqlite3.Connection) -> None:
    """If all three feeds fail, error is set and candidates is empty (FRM-CON-005)."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    site = _forum_site()
    with _client_from_handler(handler) as client:
        result = admit_from_feeds(conn, site, client=client, now=NOW)

    assert result.error is not None
    assert result.candidates == []
    # All three RSS calls were attempted and counted even though each failed —
    # fetch_count is a metric of calls made, not calls that succeeded.
    assert result.fetch_count == 3


# ---------------------------------------------------------------------------
# gather_forum: Rule-B candidate assembly (TASK-019)
# ---------------------------------------------------------------------------


def _admit_and_make_due(conn: sqlite3.Connection, topic_id: int) -> None:
    """Helper: admit a topic with first_seen_at=0 so offset=0 fires at any now."""
    forum_store.admit_topic(conn, SITE_ID, topic_id, first_seen_at=0)


def test_gather_forum_emits_rule_b_candidates(conn: sqlite3.Connection) -> None:
    """due topics with qualifying posts produce RuleBCandidates (FRM-003)."""
    # The fixture has topic 1234 with post 5001 (10 likes) and 5003 (5 likes).
    # Default like_threshold=6, so post 5001 qualifies; post 5003 does not.
    # We override like_threshold=5 so both 5001 and 5003 qualify (5003 has 5 likes).
    site = _forum_site(poll_offsets_days=(0,), like_threshold=5)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/t/") and path.endswith(".json"):
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    # Admit a topic that maps to the fixture's topic id 1234 via its JSON.
    # For gather_forum, topic_id comes from forum_watch; we admit any id and
    # the JSON path uses it to form /t/<id>.json.  The fixture returns id=1234
    # in the JSON body, but parse_topic returns the parsed topic/posts regardless.
    _admit_and_make_due(conn, 1234)

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    assert result.error is None
    # One due topic → one topic-JSON fetch counted.
    assert result.fetch_count == 1
    assert len(result.candidates) == 1
    cand = result.candidates[0]
    assert isinstance(cand, RuleBCandidate)
    assert cand.topic_id == 1234
    # Posts with like_count >= 5 (threshold) and not seen: 5001 (10) and 5003 (5).
    trigger_ids = {p.post_id for p in cand.trigger_posts}
    assert 5001 in trigger_ids
    assert 5003 in trigger_ids
    assert 5002 not in trigger_ids  # 0 likes


def test_gather_forum_candidate_url_carries_slug(conn: sqlite3.Connection) -> None:
    """Rule-B topic_url uses the parsed slug, matching the Rule-A ``/t/<slug>/<id>`` form.

    The same topic must yield the same reminder URL whichever rule surfaces it,
    so gather_forum builds ``/t/<slug>/<id>`` from the JSON slug rather than the
    slugless ``/t/<id>`` (FRM-GUD-003).  The fixture topic has slug
    ``my-forum-topic`` and id 1234.
    """
    site = _forum_site(poll_offsets_days=(0,), like_threshold=5)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/t/") and request.url.path.endswith(".json"):
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    _admit_and_make_due(conn, 1234)

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    assert result.candidates[0].topic_url == "https://forum.example.com/t/my-forum-topic/1234"


def test_gather_forum_candidate_url_falls_back_to_slugless(conn: sqlite3.Connection) -> None:
    """A payload with no slug yields the slugless ``/t/<id>`` reminder URL.

    Discourse always slugs topics, but a lite/malformed JSON degrades ``slug``
    to ``""`` (``_as_str`` default); the URL builder must fall back to the
    slugless form rather than emitting ``/t//<id>`` (FRM-GUD-003 / FRM-GUD-006).
    """
    slugless_json = (
        b'{"id": 1234, "title": "No Slug Topic", "like_count": 9, '
        b'"post_stream": {"posts": [{"id": 5001, "post_number": 1, '
        b'"cooked": "<p>Body.</p>", "actions_summary": [{"id": 2, "count": 8}]}]}}'
    )
    site = _forum_site(poll_offsets_days=(0,), like_threshold=5)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/t/") and request.url.path.endswith(".json"):
            return httpx.Response(
                200, content=slugless_json, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    _admit_and_make_due(conn, 1234)

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    assert result.candidates[0].topic_url == "https://forum.example.com/t/1234"


def test_gather_forum_effective_threshold_switches_on_interest(conn: sqlite3.Connection) -> None:
    """interest_like_threshold applies when op_interest_kept=1 (FRM-003 / TASK-018).

    Default like_threshold=6, interest_like_threshold=3.
    Post 5003 has 5 likes: qualifies under interest threshold (3) but NOT default (6).
    When op_interest_kept=1 the post must appear in the candidate; when 0 it must not.
    """
    site = _forum_site(
        poll_offsets_days=(0,),
        like_threshold=6,
        interest_like_threshold=3,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/t/") and request.url.path.endswith(".json"):
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    # --- Case A: op_interest_kept=1 → interest threshold → post 5003 qualifies ---
    _admit_and_make_due(conn, 1234)
    forum_store.set_op_verdict(conn, SITE_ID, 1234, kept=1)

    with _client_from_handler(handler) as client:
        result_a = gather_forum(conn, site, client=client, now=NOW)

    assert len(result_a.candidates) == 1
    trigger_ids_a = {p.post_id for p in result_a.candidates[0].trigger_posts}
    assert 5003 in trigger_ids_a, "post with 5 likes must qualify at interest threshold 3"
    assert result_a.candidates[0].effective_threshold == 3


def test_gather_forum_effective_threshold_default_when_interest_not_kept(
    conn: sqlite3.Connection,
) -> None:
    """Default like_threshold applies when op_interest_kept=0 or NULL (FRM-003 / TASK-018)."""
    site = _forum_site(
        poll_offsets_days=(0,),
        like_threshold=6,
        interest_like_threshold=3,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/t/") and request.url.path.endswith(".json"):
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    _admit_and_make_due(conn, 1234)
    # op_interest_kept not set (NULL) → default threshold applies.

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    assert len(result.candidates) == 1
    trigger_ids = {p.post_id for p in result.candidates[0].trigger_posts}
    assert 5003 not in trigger_ids, "post with 5 likes must NOT qualify at default threshold 6"
    assert 5001 in trigger_ids, "post with 10 likes must qualify at default threshold 6"
    assert result.candidates[0].effective_threshold == 6


def test_gather_forum_seen_posts_excluded(conn: sqlite3.Connection) -> None:
    """Already-seen posts are excluded from trigger_posts (FRM-006)."""
    site = _forum_site(poll_offsets_days=(0,), like_threshold=5)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/t/") and request.url.path.endswith(".json"):
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    _admit_and_make_due(conn, 1234)
    # Mark post 5001 (10 likes) as already seen.
    forum_store.record_post(conn, SITE_ID, topic_id=1234, post_id=5001, kept=1)

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    # Post 5001 already seen → only 5003 (5 likes ≥ threshold 5) qualifies.
    trigger_ids = {p.post_id for p in result.candidates[0].trigger_posts}
    assert 5001 not in trigger_ids
    assert 5003 in trigger_ids


def test_gather_forum_last_like_count_short_circuit(conn: sqlite3.Connection) -> None:
    """FRM-CON-004: short-circuit emits no candidate when like_count is unchanged after first poll.

    First poll (completed_polls=0, last_like_count=None): always evaluates → candidate emitted.
    Second poll (completed_polls=1): if topic.like_count == last_like_count → no candidate.
    """
    site = _forum_site(poll_offsets_days=(0, 1, 7), like_threshold=5)
    # discourse_topic.json has like_count=15 at the topic level.
    topic_json_like_count = 15

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/t/") and request.url.path.endswith(".json"):
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    _admit_and_make_due(conn, 1234)

    # --- Poll 1 (completed_polls=0, last_like_count=None): must evaluate ---
    with _client_from_handler(handler) as client:
        result1 = gather_forum(conn, site, client=client, now=NOW)

    assert len(result1.candidates) == 1, "first poll must always produce a candidate"

    # Finalize poll 1: store last_like_count=15, advance completed_polls to 1.
    forum_store.finalize_poll(
        conn, SITE_ID, 1234, like_count=topic_json_like_count, offsets=(0, 1, 7)
    )

    # --- Poll 2 (completed_polls=1, last_like_count=15, topic.like_count=15): short-circuit ---
    # offset[1]=1 day → due when now - 0 >= 86400; use now >= 86400
    due_now = 86400 + 1
    with _client_from_handler(handler) as client:
        result2 = gather_forum(conn, site, client=client, now=due_now)

    assert result2.candidates == [], (
        "second poll with unchanged like_count must be short-circuited (FRM-CON-004)"
    )


def test_gather_forum_short_circuit_does_not_fire_on_first_poll(conn: sqlite3.Connection) -> None:
    """FRM-CON-004: first poll never short-circuits even when last_like_count appears set."""
    site = _forum_site(poll_offsets_days=(0,), like_threshold=5)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/t/") and request.url.path.endswith(".json"):
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    _admit_and_make_due(conn, 1234)
    # Artificially force last_like_count=15 by directly updating the DB
    # (finalize_poll also increments completed_polls, which we don't want here).
    conn.execute(
        "UPDATE forum_watch SET last_like_count = 15 WHERE site_id = ? AND topic_id = ?",
        (SITE_ID, 1234),
    )
    conn.commit()
    # completed_polls is still 0 (not finalized), so the short-circuit must not fire.

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    assert len(result.candidates) == 1, (
        "first poll must never be short-circuited (completed_polls=0)"
    )


def test_gather_forum_json_error_absorbed(conn: sqlite3.Connection) -> None:
    """A FetchError on a topic JSON is absorbed into error; other topics continue (FRM-CON-005)."""
    site = _forum_site(poll_offsets_days=(0,), like_threshold=5)

    _admit_and_make_due(conn, 1234)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    assert result.error is not None
    assert result.candidates == []


def test_gather_forum_continues_after_one_topic_json_failure(conn: sqlite3.Connection) -> None:
    """A JSON failure for one topic does not abort remaining topics (FRM-CON-005)."""
    site = _forum_site(poll_offsets_days=(0,), like_threshold=5)

    # Admit two topics; their order in due_topics is first_seen_at ASC, topic_id ASC.
    _admit_and_make_due(conn, 1111)
    _admit_and_make_due(conn, 1234)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/t/1111.json":
            return httpx.Response(503, text="down")  # 1111 fails
        if path == "/t/1234.json":
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    assert result.error is not None, "error must mention the failed topic"
    # Topic 1234 succeeded and has qualifying posts.
    assert any(c.topic_id == 1234 for c in result.candidates), (
        "surviving topic must still produce a candidate"
    )
    assert all(c.topic_id != 1111 for c in result.candidates), (
        "failed topic must not produce a candidate"
    )
    # Both due topics were fetched (the failed 1111 and the succeeding 1234), so
    # both calls are counted even though one raised.
    assert result.fetch_count == 2


def test_gather_forum_no_qualifying_posts_emits_no_candidate(conn: sqlite3.Connection) -> None:
    """A due topic with no qualifying unseen posts produces no RuleBCandidate (FRM-003)."""
    # Use a very high threshold so no posts qualify.
    site = _forum_site(poll_offsets_days=(0,), like_threshold=100)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/t/") and request.url.path.endswith(".json"):
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    _admit_and_make_due(conn, 1234)

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    assert result.candidates == []
    assert result.error is None


# ---------------------------------------------------------------------------
# No writes during gather_forum (TASK-019 / FRM-CON-005)
# ---------------------------------------------------------------------------


def test_gather_forum_writes_nothing_to_db(conn: sqlite3.Connection) -> None:
    """gather_forum must not write to forum_watch or forum_post_seen (FRM-CON-005).

    Snapshot row counts before and after the call and assert they are equal.
    """
    site = _forum_site(poll_offsets_days=(0,), like_threshold=5)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/t/") and request.url.path.endswith(".json"):
            return httpx.Response(
                200, content=_TOPIC_JSON, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    _admit_and_make_due(conn, 1234)

    watch_before = conn.execute("SELECT * FROM forum_watch").fetchall()
    post_seen_before = conn.execute("SELECT * FROM forum_post_seen").fetchall()

    with _client_from_handler(handler) as client:
        result = gather_forum(conn, site, client=client, now=NOW)

    watch_after = conn.execute("SELECT * FROM forum_watch").fetchall()
    post_seen_after = conn.execute("SELECT * FROM forum_post_seen").fetchall()

    assert watch_after == watch_before, "gather_forum must not modify forum_watch"
    assert post_seen_after == post_seen_before, "gather_forum must not modify forum_post_seen"
    # Sanity: a candidate was actually produced (so the function did work).
    assert len(result.candidates) > 0


# ---------------------------------------------------------------------------
# op_interest_kept reader in forum_store (added as part of TASK-016 support)
# ---------------------------------------------------------------------------


def test_op_interest_kept_reader_returns_none_before_verdict(conn: sqlite3.Connection) -> None:
    """forum_store.op_interest_kept returns None before any set_op_verdict call."""
    from feed_filter.forum_store import op_interest_kept

    forum_store.admit_topic(conn, SITE_ID, 999, first_seen_at=NOW)
    assert op_interest_kept(conn, SITE_ID, 999) is None


def test_op_interest_kept_reader_returns_verdict(conn: sqlite3.Connection) -> None:
    """forum_store.op_interest_kept returns the stored verdict (1 or 0)."""
    from feed_filter.forum_store import op_interest_kept

    forum_store.admit_topic(conn, SITE_ID, 998, first_seen_at=NOW)
    forum_store.set_op_verdict(conn, SITE_ID, 998, kept=1)
    assert op_interest_kept(conn, SITE_ID, 998) == 1

    forum_store.set_op_verdict(conn, SITE_ID, 998, kept=0)
    assert op_interest_kept(conn, SITE_ID, 998) == 0


def test_op_interest_kept_reader_returns_none_for_unknown_topic(conn: sqlite3.Connection) -> None:
    """forum_store.op_interest_kept returns None for a topic not in forum_watch."""
    from feed_filter.forum_store import op_interest_kept

    assert op_interest_kept(conn, SITE_ID, 99999) is None
