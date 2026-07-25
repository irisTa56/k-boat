"""Behavior tests for the SQLite seen-store."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from feed_filter import seen
from feed_filter.canonical import CanonicalUrl


def _url(s: str) -> CanonicalUrl:
    # Keys are canonical by construction in these store-mechanics tests; the
    # canonicalization itself is covered in test_canonical.py.
    return CanonicalUrl(s)


def _apply(conn: sqlite3.Connection, upto: int) -> None:
    """Hand-build a DB at version ``upto`` by running migrations 0..upto-1.

    Lets the next migration be exercised as an upgrade over pre-existing data,
    rather than as part of a fresh all-at-once create.
    """
    for migration in seen._MIGRATIONS[:upto]:
        for statement in migration:
            conn.execute(statement)
    conn.execute(f"PRAGMA user_version = {upto}")


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
    """The v3 migration adds forum_watch.poll_eligible, defaulting to 0."""
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(forum_watch)")}
    assert "poll_eligible" in cols, "v3 migration must add the poll_eligible column"
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk).
    assert cols["poll_eligible"][4] == "0", "poll_eligible must default to 0 (not polled)"


def test_v3_upgrade_defaults_existing_rows_to_not_poll_eligible(tmp_path: Path) -> None:
    """An existing v2 DB with rows gains poll_eligible=0 on the v3 upgrade.

    Pins the externally-important upgrade behavior: a topic already being watched
    under the old (all-latest) schema stops being polled until it is re-seen in a
    top feed. Builds a v2 DB by hand (migrations 0..1, user_version=2) so the v3
    ALTER is exercised against pre-existing data, not a fresh all-at-once create.
    """
    path = tmp_path / "v2.db"
    raw = sqlite3.connect(path)
    _apply(raw, 2)  # v1: seen, v2: forum tables (no poll_eligible)
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


def test_v4_migration_adds_site_health_table(conn: sqlite3.Connection) -> None:
    """The v4 migration creates site_health(site_id PK, consecutive_failures)."""
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(site_health)")}
    assert cols, "v4 migration must create the site_health table"
    assert set(cols) == {"site_id", "consecutive_failures"}
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk).
    assert cols["site_id"][5] == 1, "site_id must be the primary key"
    assert cols["consecutive_failures"][4] == "0", "consecutive_failures must default to 0"


def test_v4_migration_applies_over_v3_without_touching_existing_tables(tmp_path: Path) -> None:
    """A v3 DB with forum rows gains site_health on the v4 upgrade, losing nothing.

    Builds a v3 DB by hand (migrations 0..2, user_version=3) so the v4 CREATE is
    exercised as an append over pre-existing data, and asserts the pre-existing
    forum_watch row survives — the append-only migration must not rebuild tables.
    """
    path = tmp_path / "v3.db"
    raw = sqlite3.connect(path)
    _apply(raw, 3)  # v1: seen, v2: forum tables, v3: poll_eligible
    raw.execute(
        "INSERT INTO forum_watch (site_id, topic_id, first_seen_at, completed_polls, retired) "
        "VALUES ('s1', 42, 1000, 0, 0)"
    )
    raw.commit()
    raw.close()

    conn = seen.open_db(path)  # applies the v4 migration over existing data
    try:
        (version,) = conn.execute("PRAGMA user_version").fetchone()
        assert version == len(seen._MIGRATIONS), "upgrade must advance to the latest version"
        tables = {row[1] for row in conn.execute("PRAGMA table_info(site_health)")}
        assert tables == {"site_id", "consecutive_failures"}, "v4 must add site_health"
        survived = conn.execute("SELECT topic_id FROM forum_watch WHERE site_id = 's1'").fetchone()
        assert survived is not None and survived[0] == 42, "existing forum rows must survive v4"
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


def test_failed_migration_rolls_back_so_the_next_open_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration that dies partway leaves the DB at its previous version.

    Each migration's statements and its ``user_version`` stamp share one
    transaction, so "DDL applied, version not stamped" is not a reachable state.
    Without that, an interrupted v3 would leave ``poll_eligible`` added with the
    version still at 2, and every later open would re-run the ALTER and die on
    ``duplicate column name`` — a permanently wedged store, recoverable only by
    hand. The crash is injected as a statement failing *after* the real DDL, which
    is what a process killed mid-migration looks like to the database.
    """
    path = tmp_path / "v2.db"
    raw = sqlite3.connect(path)
    _apply(raw, 2)  # v1: seen, v2: forum tables (no poll_eligible)
    raw.commit()
    raw.close()

    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def spy(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    crashing = list(seen._MIGRATIONS)
    crashing[2] = (*crashing[2], "INSERT INTO no_such_table VALUES (1)")
    monkeypatch.setattr(seen, "_MIGRATIONS", crashing)
    monkeypatch.setattr(seen.sqlite3, "connect", spy)
    with pytest.raises(sqlite3.OperationalError):
        seen.open_db(path)
    monkeypatch.undo()

    # A failed open returns no handle, so nothing else can close one it left behind
    # — and an open handle holds the WAL sidecars and the write lock.
    with pytest.raises(sqlite3.ProgrammingError):
        opened[-1].execute("SELECT 1")

    raw = sqlite3.connect(path)
    try:
        (version,) = raw.execute("PRAGMA user_version").fetchone()
        assert version == 2, "the interrupted migration must not have stamped its version"
        cols = {row[1] for row in raw.execute("PRAGMA table_info(forum_watch)")}
        assert "poll_eligible" not in cols, "its DDL must roll back with the stamp"
    finally:
        raw.close()

    conn = seen.open_db(path)  # recovers by re-running v3 over a clean v2
    try:
        (version,) = conn.execute("PRAGMA user_version").fetchone()
        assert version == len(seen._MIGRATIONS)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(forum_watch)")}
        assert "poll_eligible" in cols, "the retry must apply the rolled-back migration"
    finally:
        conn.close()


def test_opener_that_loses_the_migration_race_skips_the_applied_step(tmp_path: Path) -> None:
    """Two openers migrating the same DB at once: the loser must not re-apply.

    Both read ``user_version`` before contending for the write lock, so the loser
    holds a stale bound. Re-running v3's ALTER over the winner's database would die
    on ``duplicate column name`` (the other migrations are ``IF NOT EXISTS``, so
    only an ALTER exposes this) and fail whichever run lost — the scheduled routine
    overlapping a manual invocation on the first run after a schema release.

    The interleaving is forced deterministically rather than with threads: a trace
    callback on the loser's connection lets the winner migrate the file to
    completion at the instant the loser's ``BEGIN IMMEDIATE`` starts, the widest
    that window ever gets. Driving ``_migrate`` directly is what makes the callback
    placeable between connect and migrate; the assertions are on database state.
    """
    path = tmp_path / "v2.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA journal_mode=WAL")  # as open_db leaves it, so the race is real
    _apply(raw, 2)
    raw.commit()
    raw.close()

    loser = sqlite3.connect(path)
    raced = False

    def race_ahead(statement: str) -> None:
        nonlocal raced
        if statement.startswith("BEGIN") and not raced:
            raced = True
            seen.open_db(path).close()  # the winner migrates all the way

    loser.set_trace_callback(race_ahead)
    try:
        seen._migrate(loser)  # must not raise duplicate column name
        assert raced, "the winner never ran — the test would pass without the race"
        (version,) = loser.execute("PRAGMA user_version").fetchone()
        assert version == len(seen._MIGRATIONS)
    finally:
        loser.close()


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
    # Snapshot rows carry kept=NULL (the flood guard).
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
