"""Behavior tests for forum_store.py — post-grain dedupe and watch/poll throttle.

All scheduling functions receive an injected ``now`` (unix int) so tests never
call ``time.time()`` (FRM-PAT-002).  The ``conn`` fixture uses ``seen.open_db``
so the v2 migration runs and the forum tables are present (FRM-GUD-004).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from feed_filter import forum_store, seen

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SITE = "erlangforums"
OFFSETS = (0, 1, 7)  # seconds → days in the due_topics logic


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = seen.open_db(tmp_path / "feed-filter.db")
    yield c
    c.close()


# ---------------------------------------------------------------------------
# TASK-011 sanity: v2 migration created the tables
# ---------------------------------------------------------------------------


def test_forum_tables_exist(conn: sqlite3.Connection) -> None:
    """The v2 migration must create forum_watch and forum_post_seen (TASK-011)."""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "forum_watch" in tables
    assert "forum_post_seen" in tables


# ---------------------------------------------------------------------------
# TASK-012: admission
# ---------------------------------------------------------------------------


def test_admit_topic_inserts_row(conn: sqlite3.Connection) -> None:
    """admit_topic writes one row with the given first_seen_at."""
    forum_store.admit_topic(conn, SITE, 42, first_seen_at=1000)
    row = conn.execute(
        "SELECT first_seen_at, completed_polls, retired, op_interest_kept, last_like_count "
        "FROM forum_watch WHERE site_id = ? AND topic_id = ?",
        (SITE, 42),
    ).fetchone()
    assert row is not None
    first_seen_at, completed_polls, retired, op_interest_kept, last_like_count = row
    assert first_seen_at == 1000
    assert completed_polls == 0
    assert retired == 0
    assert op_interest_kept is None
    assert last_like_count is None


def test_admit_topic_idempotent(conn: sqlite3.Connection) -> None:
    """Second admit_topic for the same key is a no-op (FRM-CON-005).

    first_seen_at and completed_polls must not be reset.
    """
    forum_store.admit_topic(conn, SITE, 42, first_seen_at=1000)
    # Simulate a poll having been finalized before a duplicate admission.
    forum_store.finalize_poll(conn, SITE, 42, like_count=5, offsets=OFFSETS)
    # Second admission with a different first_seen_at must be ignored.
    forum_store.admit_topic(conn, SITE, 42, first_seen_at=9999)

    row = conn.execute(
        "SELECT first_seen_at, completed_polls FROM forum_watch WHERE site_id = ? AND topic_id = ?",
        (SITE, 42),
    ).fetchone()
    assert row is not None
    assert row[0] == 1000, "first_seen_at must not be overwritten"
    assert row[1] == 1, "completed_polls must not be reset"


def test_set_op_verdict_round_trip(conn: sqlite3.Connection) -> None:
    """set_op_verdict writes and op_interest_kept can be read back (FRM-002)."""
    forum_store.admit_topic(conn, SITE, 10, first_seen_at=500)
    assert _op_verdict(conn, SITE, 10) is None  # unset before judgment

    forum_store.set_op_verdict(conn, SITE, 10, kept=1)
    assert _op_verdict(conn, SITE, 10) == 1

    forum_store.set_op_verdict(conn, SITE, 10, kept=0)
    assert _op_verdict(conn, SITE, 10) == 0


# ---------------------------------------------------------------------------
# TASK-013: post dedupe
# ---------------------------------------------------------------------------


def test_post_not_seen_initially(conn: sqlite3.Connection) -> None:
    """is_post_seen returns False before any record_post call (FRM-006)."""
    assert not forum_store.is_post_seen(conn, SITE, post_id=99)


def test_record_post_marks_seen(conn: sqlite3.Connection) -> None:
    """is_post_seen returns True after record_post (FRM-006)."""
    forum_store.admit_topic(conn, SITE, 5, first_seen_at=100)
    forum_store.record_post(conn, SITE, topic_id=5, post_id=99, kept=1)
    assert forum_store.is_post_seen(conn, SITE, post_id=99)


def test_record_post_upsert_updates_kept(conn: sqlite3.Connection) -> None:
    """Re-recording the same post updates kept without duplicating the row."""
    forum_store.admit_topic(conn, SITE, 5, first_seen_at=100)
    forum_store.record_post(conn, SITE, topic_id=5, post_id=99, kept=1)
    forum_store.record_post(conn, SITE, topic_id=5, post_id=99, kept=0)

    row = conn.execute(
        "SELECT kept, topic_id FROM forum_post_seen WHERE site_id = ? AND post_id = ?",
        (SITE, 99),
    ).fetchone()
    assert row is not None
    assert row[0] == 0, "kept must be updated to 0 after the upsert"
    assert row[1] == 5, "topic_id must remain stored through the upsert"

    count = conn.execute(
        "SELECT COUNT(*) FROM forum_post_seen WHERE site_id = ? AND post_id = ?",
        (SITE, 99),
    ).fetchone()[0]
    assert count == 1, "upsert must not duplicate the row"


def test_record_post_independent_of_watch(conn: sqlite3.Connection) -> None:
    """The record path does not depend on a prior admit_topic (FRM-CON-005).

    forum_post_seen is a standalone dedupe authority: a post can be recorded
    seen without any forum_watch parent row (there is no FK), so a crash that
    loses the watch row never resurrects an already-dispositioned post.
    """
    assert not forum_store.is_post_seen(conn, SITE, post_id=1)
    forum_store.record_post(conn, SITE, topic_id=404, post_id=1, kept=1)
    assert forum_store.is_post_seen(conn, SITE, post_id=1)
    # No forum_watch row was ever created for this topic.
    watch = conn.execute(
        "SELECT 1 FROM forum_watch WHERE site_id = ? AND topic_id = ?",
        (SITE, 404),
    ).fetchone()
    assert watch is None


# ---------------------------------------------------------------------------
# TASK-014: scheduling — due_topics and finalize_poll
# ---------------------------------------------------------------------------


def test_not_due_before_first_offset(conn: sqlite3.Connection) -> None:
    """A topic is not due before offset[0] days have elapsed (FRM-007).

    OFFSETS=(0,1,7) means offset[0]=0 days, so any elapsed time ≥ 0 qualifies.
    Use a non-zero first offset to test the "not yet due" case.
    """
    offsets = (1, 7)  # must wait at least 1 day before the first poll
    admitted_at = 0
    forum_store.admit_topic(conn, SITE, 1, first_seen_at=admitted_at)

    # 23 hours elapsed — not yet 1 day
    now = admitted_at + 23 * 3600
    due = forum_store.due_topics(conn, SITE, offsets, now)
    assert due == []


def test_due_at_first_offset(conn: sqlite3.Connection) -> None:
    """A topic is due exactly at offset[0] (FRM-007)."""
    offsets = (1, 7)
    admitted_at = 0
    forum_store.admit_topic(conn, SITE, 1, first_seen_at=admitted_at)

    now = admitted_at + 1 * 86400  # exactly 1 day elapsed
    due = forum_store.due_topics(conn, SITE, offsets, now)
    assert len(due) == 1
    assert due[0]["topic_id"] == 1
    assert due[0]["completed_polls"] == 0


def test_due_offset_zero(conn: sqlite3.Connection) -> None:
    """Offset 0 means the topic is due immediately on admission (OFFSETS default)."""
    forum_store.admit_topic(conn, SITE, 7, first_seen_at=1000)
    # now == first_seen_at → elapsed = 0 >= 0 * 86400 = 0
    due = forum_store.due_topics(conn, SITE, OFFSETS, now=1000)
    assert any(r["topic_id"] == 7 for r in due)


def test_offline_catchup_collapses_to_one_poll(conn: sqlite3.Connection) -> None:
    """Several offsets elapsed during downtime → still exactly one due poll.

    After finalize_poll advances completed_polls from 0 to 1, the topic is
    checked against offsets[1], not offsets[2] — so it only becomes due once
    per finalize step, even if multiple offsets have elapsed (FRM-007).
    """
    offsets = (0, 1, 7)
    admitted_at = 0
    forum_store.admit_topic(conn, SITE, 3, first_seen_at=admitted_at)

    # 10 days elapsed — offsets 0, 1, and 7 have all elapsed.
    now = admitted_at + 10 * 86400

    due = forum_store.due_topics(conn, SITE, offsets, now)
    assert len(due) == 1, "multiple elapsed offsets must collapse to one due poll"
    assert due[0]["completed_polls"] == 0

    # Finalize the poll — completed_polls becomes 1, checked against offsets[1]=1d.
    forum_store.finalize_poll(conn, SITE, 3, like_count=2, offsets=offsets)

    # Still 10 days elapsed from admission; offsets[1]=1 day → still due.
    due2 = forum_store.due_topics(conn, SITE, offsets, now)
    assert len(due2) == 1
    assert due2[0]["completed_polls"] == 1

    # Finalize again — completed_polls becomes 2, checked against offsets[2]=7d.
    forum_store.finalize_poll(conn, SITE, 3, like_count=3, offsets=offsets)
    due3 = forum_store.due_topics(conn, SITE, offsets, now)
    assert len(due3) == 1
    assert due3[0]["completed_polls"] == 2


def test_retirement_at_last_offset(conn: sqlite3.Connection) -> None:
    """A topic is retired when completed_polls >= len(offsets), not before (FRM-007)."""
    offsets = (0, 1, 7)
    admitted_at = 0
    forum_store.admit_topic(conn, SITE, 20, first_seen_at=admitted_at)
    now = admitted_at + 30 * 86400  # well past all offsets

    # Poll 0 → 1 → 2 → retired
    for poll in range(len(offsets)):
        due = forum_store.due_topics(conn, SITE, offsets, now)
        assert any(r["topic_id"] == 20 for r in due), f"should be due before poll {poll}"
        forum_store.finalize_poll(conn, SITE, 20, like_count=0, offsets=offsets)

    # After the 3rd finalize completed_polls == 3 == len(offsets) → retired.
    retired = conn.execute(
        "SELECT retired FROM forum_watch WHERE site_id = ? AND topic_id = ?",
        (SITE, 20),
    ).fetchone()[0]
    assert retired == 1

    due_after = forum_store.due_topics(conn, SITE, offsets, now)
    assert not any(r["topic_id"] == 20 for r in due_after), "retired topic must not appear in due"


def test_due_topics_excludes_retired(conn: sqlite3.Connection) -> None:
    """due_topics never returns a retired topic regardless of elapsed time (FRM-007)."""
    offsets = (0,)  # single poll → retires immediately after finalize
    forum_store.admit_topic(conn, SITE, 50, first_seen_at=0)
    forum_store.finalize_poll(conn, SITE, 50, like_count=0, offsets=offsets)

    due = forum_store.due_topics(conn, SITE, offsets, now=999999)
    assert not any(r["topic_id"] == 50 for r in due)


def test_due_topics_survives_shortened_offsets(conn: sqlite3.Connection) -> None:
    """A non-retired topic past the end of a *shortened* offsets list is skipped,
    not crashed (FRM-007).

    If poll_offsets_days is shortened in config after a topic has already
    completed more polls than the new schedule has offsets, indexing
    offsets[completed_polls] would raise IndexError. due_topics must guard it
    and simply drop the topic from the due set.
    """
    # Poll twice under a 3-offset schedule → completed_polls=2, not retired.
    forum_store.admit_topic(conn, SITE, 30, first_seen_at=0)
    now = 30 * 86400
    forum_store.finalize_poll(conn, SITE, 30, like_count=0, offsets=(0, 1, 7))
    forum_store.finalize_poll(conn, SITE, 30, like_count=0, offsets=(0, 1, 7))
    completed = conn.execute(
        "SELECT completed_polls, retired FROM forum_watch WHERE site_id = ? AND topic_id = ?",
        (SITE, 30),
    ).fetchone()
    assert completed == (2, 0), "precondition: 2 polls done, not retired"

    # Now the schedule is shortened to a single offset; completed_polls (2) is
    # past its end. Must not raise, and the topic must not be returned.
    due = forum_store.due_topics(conn, SITE, (0,), now)
    assert not any(r["topic_id"] == 30 for r in due)


def test_due_topics_returns_op_interest_and_like_count(conn: sqlite3.Connection) -> None:
    """due_topics rows include op_interest_kept and last_like_count (FRM-CON-004)."""
    forum_store.admit_topic(conn, SITE, 11, first_seen_at=0)
    forum_store.set_op_verdict(conn, SITE, 11, kept=1)
    forum_store.finalize_poll(conn, SITE, 11, like_count=7, offsets=OFFSETS)

    # After poll 0 → check poll 1 at offset 1d
    now = 2 * 86400
    due = forum_store.due_topics(conn, SITE, OFFSETS, now)
    match = next(r for r in due if r["topic_id"] == 11)
    assert match["op_interest_kept"] == 1
    assert match["last_like_count"] == 7


def test_due_topics_deterministic_order_on_first_seen_tie(conn: sqlite3.Connection) -> None:
    """Topics sharing first_seen_at come back in ascending topic_id order.

    Topics admitted in one run share the same injected ``now`` as their
    first_seen_at, so the topic_id tiebreak is what makes the due set's order
    (and the downstream round-robin slotting) reproducible.
    """
    # Admit out of id order, all at the same first_seen_at.
    for topic_id in (30, 10, 20):
        forum_store.admit_topic(conn, SITE, topic_id, first_seen_at=0)

    due = forum_store.due_topics(conn, SITE, OFFSETS, now=0)
    assert [r["topic_id"] for r in due] == [10, 20, 30]


def test_empty_offsets_returns_nothing(conn: sqlite3.Connection) -> None:
    """due_topics with an empty offsets tuple is safe and returns []."""
    forum_store.admit_topic(conn, SITE, 60, first_seen_at=0)
    assert forum_store.due_topics(conn, SITE, (), now=99999) == []


# ---------------------------------------------------------------------------
# TASK-014: last_like_count
# ---------------------------------------------------------------------------


def test_last_like_count_unset_initially(conn: sqlite3.Connection) -> None:
    """last_like_count returns None before any finalize_poll (FRM-CON-004)."""
    forum_store.admit_topic(conn, SITE, 77, first_seen_at=0)
    assert forum_store.last_like_count(conn, SITE, 77) is None


def test_last_like_count_after_finalize(conn: sqlite3.Connection) -> None:
    """last_like_count returns the value stored by finalize_poll (FRM-CON-004)."""
    forum_store.admit_topic(conn, SITE, 77, first_seen_at=0)
    forum_store.finalize_poll(conn, SITE, 77, like_count=13, offsets=OFFSETS)
    assert forum_store.last_like_count(conn, SITE, 77) == 13


def test_last_like_count_missing_topic_returns_none(conn: sqlite3.Connection) -> None:
    """last_like_count returns None for a topic not in the watch set."""
    assert forum_store.last_like_count(conn, SITE, 999) is None


def test_finalize_poll_on_missing_topic_is_noop(conn: sqlite3.Connection) -> None:
    """finalize_poll on an unknown topic must not raise (FRM-CON-005 guard)."""
    forum_store.finalize_poll(conn, SITE, 9999, like_count=0, offsets=OFFSETS)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _op_verdict(conn: sqlite3.Connection, site_id: str, topic_id: int) -> int | None:
    row = conn.execute(
        "SELECT op_interest_kept FROM forum_watch WHERE site_id = ? AND topic_id = ?",
        (site_id, topic_id),
    ).fetchone()
    return None if row is None else row[0]
