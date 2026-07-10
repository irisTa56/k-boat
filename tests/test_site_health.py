"""Behavior tests for the site_health store — the durable failure counter (SH-TEST-001).

Network-free; the counter lives in a real tmp_path SQLite DB via ``seen.open_db``
so the v4 ``site_health`` table exists (mirrors ``test_forum_store.py``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from feed_filter import seen, site_health


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = seen.open_db(tmp_path / "feed-filter.db")
    yield c
    c.close()


def _count(conn: sqlite3.Connection, site_id: str) -> int | None:
    row = conn.execute(
        "SELECT consecutive_failures FROM site_health WHERE site_id = ?", (site_id,)
    ).fetchone()
    return None if row is None else row[0]


def test_record_failure_starts_at_one(conn: sqlite3.Connection) -> None:
    assert site_health.record_failure(conn, "s1") == 1
    assert _count(conn, "s1") == 1


def test_record_failure_accumulates(conn: sqlite3.Connection) -> None:
    assert site_health.record_failure(conn, "s1") == 1
    assert site_health.record_failure(conn, "s1") == 2
    assert site_health.record_failure(conn, "s1") == 3
    assert _count(conn, "s1") == 3


def test_record_failure_is_per_site(conn: sqlite3.Connection) -> None:
    site_health.record_failure(conn, "s1")
    site_health.record_failure(conn, "s1")
    assert site_health.record_failure(conn, "s2") == 1, "each site counts independently"
    assert _count(conn, "s1") == 2


def test_record_success_resets(conn: sqlite3.Connection) -> None:
    site_health.record_failure(conn, "s1")
    site_health.record_failure(conn, "s1")
    site_health.record_success(conn, "s1")
    assert _count(conn, "s1") == 0
    # A failure after a reset starts the streak over, not from the old value.
    assert site_health.record_failure(conn, "s1") == 1


def test_record_success_on_absent_site_is_noop(conn: sqlite3.Connection) -> None:
    """Resetting a site that has never failed must not insert a zero row (SH-REQ-002)."""
    site_health.record_success(conn, "never-failed")
    assert _count(conn, "never-failed") is None, "an unfailed site stays absent, not a 0 row"


def test_is_persistent_boundary() -> None:
    """False below the threshold, True at exactly the threshold (SH-REQ-004)."""
    assert site_health.is_persistent(2, 3) is False
    assert site_health.is_persistent(3, 3) is True
    assert site_health.is_persistent(4, 3) is True
