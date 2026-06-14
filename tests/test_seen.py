"""Behavior tests for the SQLite seen-store (TASK-011)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from feed_filter import seen
from feed_filter.canonical import CanonicalUrl


def _url(s: str) -> CanonicalUrl:
    # Keys are canonical by construction in these store-mechanics tests; the
    # canonicalization itself is covered in test_canonical.py.
    return CanonicalUrl(s)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = seen.open_db(tmp_path / "feed-filter.db")
    yield c
    c.close()


def _kept(conn: sqlite3.Connection, url: CanonicalUrl) -> int | None:
    row = conn.execute("SELECT kept FROM seen WHERE canonical_url = ?", (url,)).fetchone()
    return None if row is None else row[0]


def test_migration_creates_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(seen)")}
    assert cols == {"canonical_url", "site_id", "title", "kept", "seen_at"}


def test_v3_migration_adds_poll_eligible_column(conn: sqlite3.Connection) -> None:
    """The v3 migration adds forum_watch.poll_eligible, defaulting to 0 (FRM-001)."""
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(forum_watch)")}
    assert "poll_eligible" in cols, "v3 migration must add the poll_eligible column"
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk).
    assert cols["poll_eligible"][4] == "0", "poll_eligible must default to 0 (not polled)"


def test_v3_upgrade_defaults_existing_rows_to_not_poll_eligible(tmp_path: Path) -> None:
    """An existing v2 DB with rows gains poll_eligible=0 on the v3 upgrade (FRM-001).

    Pins the externally-important upgrade behavior: a topic already being watched
    under the old (all-latest) schema stops being polled until it is re-seen in a
    top feed. Builds a v2 DB by hand (migrations 0..1, user_version=2) so the v3
    ALTER is exercised against pre-existing data, not a fresh all-at-once create.
    """
    path = tmp_path / "v2.db"
    raw = sqlite3.connect(path)
    raw.executescript(seen._MIGRATIONS[0])  # v1: seen
    raw.executescript(seen._MIGRATIONS[1])  # v2: forum tables (no poll_eligible)
    raw.execute("PRAGMA user_version = 2")
    raw.execute(
        "INSERT INTO forum_watch (site_id, topic_id, first_seen_at, completed_polls, retired) "
        "VALUES ('s1', 42, 1000, 0, 0)"
    )
    raw.commit()
    raw.close()

    conn = seen.open_db(path)  # applies the v3 migration over existing data
    try:
        (version,) = conn.execute("PRAGMA user_version").fetchone()
        assert version == len(seen._MIGRATIONS), "upgrade must advance to the latest version"
        flag = conn.execute(
            "SELECT poll_eligible FROM forum_watch WHERE site_id = 's1' AND topic_id = 42"
        ).fetchone()[0]
        assert flag == 0, "a pre-existing watched topic must default to not-poll-eligible"
    finally:
        conn.close()


def test_migration_stamps_user_version(tmp_path: Path) -> None:
    # open_db must advance user_version so future migrations are gated, not
    # silently skipped against an existing DB.
    c = seen.open_db(tmp_path / "v.db")
    try:
        (version,) = c.execute("PRAGMA user_version").fetchone()
        assert version == len(seen._MIGRATIONS)
    finally:
        c.close()


def test_reopen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "feed-filter.db"
    c1 = seen.open_db(path)
    seen.record(c1, _url("https://example.com/a"), site_id="s1", title="A", kept=1)
    c1.close()
    # Re-opening an existing store must not re-run migrations destructively.
    c2 = seen.open_db(path)
    try:
        assert seen.is_seen(c2, _url("https://example.com/a"))
        assert seen.count(c2) == 1
    finally:
        c2.close()


def test_fresh_db_reports_unseen(conn: sqlite3.Connection) -> None:
    assert seen.count(conn) == 0
    assert not seen.is_seen(conn, _url("https://example.com/a"))


def test_record_then_is_seen(conn: sqlite3.Connection) -> None:
    url = _url("https://example.com/a")
    seen.record(conn, url, site_id="s1", title="A", kept=1)
    assert seen.is_seen(conn, url)
    assert seen.count(conn) == 1


def test_record_is_idempotent(conn: sqlite3.Connection) -> None:
    url = _url("https://example.com/a")
    seen.record(conn, url, site_id="s1", title="A", kept=0)
    seen.record(conn, url, site_id="s1", title="A", kept=0)
    assert seen.count(conn) == 1
    assert seen.is_seen(conn, url)


def test_record_upsert_updates_kept(conn: sqlite3.Connection) -> None:
    url = _url("https://example.com/a")
    seen.record(conn, url, site_id="s1", title=None, kept=None)
    assert _kept(conn, url) is None
    seen.record(conn, url, site_id="s1", title="A", kept=1)
    assert _kept(conn, url) == 1
    assert seen.count(conn) == 1


def test_snapshot_marks_many_unseen(conn: sqlite3.Connection) -> None:
    urls = [_url(f"https://example.com/{i}") for i in range(5)]
    seen.snapshot(conn, site_id="s1", urls=urls)
    assert seen.count(conn) == 5
    assert all(seen.is_seen(conn, u) for u in urls)
    # Snapshot rows carry kept=NULL (the flood guard, REQ-002).
    assert _kept(conn, urls[0]) is None


def test_snapshot_does_not_clobber_existing(conn: sqlite3.Connection) -> None:
    url = _url("https://example.com/a")
    seen.record(conn, url, site_id="s1", title="A", kept=1)
    seen.snapshot(conn, site_id="s1", urls=[url])
    # The decided keep survives a later snapshot of the same URL.
    assert _kept(conn, url) == 1
    assert seen.count(conn) == 1


def test_snapshot_empty_is_noop(conn: sqlite3.Connection) -> None:
    seen.snapshot(conn, site_id="s1", urls=[])
    assert seen.count(conn) == 0
