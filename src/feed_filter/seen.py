"""The seen-store — the sole dedupe authority (REQ-009).

A single SQLite table keyed by canonical URL. A row exists iff the URL has been
processed (kept, dropped, or snapshotted at registration). ``kept`` distinguishes
the three: ``1`` reminded, ``0`` dropped, ``NULL`` snapshotted-only (the
cold-start flood guard, REQ-002 / REQ-006). Membership — not ``kept`` — is what
``is_seen`` tests.

Keys are typed ``CanonicalUrl`` so a caller that forgets to canonicalize is a
``ty`` error, not a silent duplicate reminder.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from feed_filter.canonical import CanonicalUrl

# Ordered schema migrations. ``open_db`` applies every entry past the DB's
# current ``PRAGMA user_version``, then stamps the new version — so adding a
# column in a later phase is an append here, not a break against an existing
# feed-filter.db. v1 uses IF NOT EXISTS so it is also safe over a pre-versioning
# database created during Phase 2 development.
_MIGRATIONS: list[str] = [
    # v1: initial schema
    """
    CREATE TABLE IF NOT EXISTS seen (
        canonical_url TEXT PRIMARY KEY,
        site_id       TEXT,
        title         TEXT,
        kept          INTEGER,
        seen_at       INTEGER
    );
    """,
]


def _migrate(conn: sqlite3.Connection) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    for target in range(version, len(_MIGRATIONS)):
        conn.executescript(_MIGRATIONS[target])
        # PRAGMA can't be parameterized; target+1 is a controlled int.
        conn.execute(f"PRAGMA user_version = {target + 1}")
    conn.commit()


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating + migrating) the seen-store at ``path`` in WAL mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate(conn)
    return conn


def is_seen(conn: sqlite3.Connection, url: CanonicalUrl) -> bool:
    """True iff ``url`` has a row in the store."""
    row = conn.execute("SELECT 1 FROM seen WHERE canonical_url = ?", (url,)).fetchone()
    return row is not None


def record(
    conn: sqlite3.Connection,
    url: CanonicalUrl,
    site_id: str,
    title: str | None,
    kept: int | None,
) -> None:
    """Idempotent upsert of a fully-processed URL; commits on success.

    Re-recording the same URL overwrites its metadata rather than erroring, so a
    snapshot row (``kept=NULL``) can later be promoted to a keep/drop.
    """
    conn.execute(
        """
        INSERT INTO seen (canonical_url, site_id, title, kept, seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(canonical_url) DO UPDATE SET
            site_id = excluded.site_id,
            title   = excluded.title,
            kept    = excluded.kept,
            seen_at = excluded.seen_at
        """,
        (url, site_id, title, kept, int(time.time())),
    )
    conn.commit()


def snapshot(conn: sqlite3.Connection, site_id: str, urls: list[CanonicalUrl]) -> None:
    """Bulk-mark ``urls`` as seen with ``kept=NULL`` and no reminders (REQ-002).

    Existing rows are left untouched (``DO NOTHING``), so a snapshot never
    clobbers an already-decided keep/drop.
    """
    now = int(time.time())
    conn.executemany(
        """
        INSERT INTO seen (canonical_url, site_id, title, kept, seen_at)
        VALUES (?, ?, NULL, NULL, ?)
        ON CONFLICT(canonical_url) DO NOTHING
        """,
        [(url, site_id, now) for url in urls],
    )
    conn.commit()


def count(conn: sqlite3.Connection) -> int:
    """Total number of rows in the store."""
    (n,) = conn.execute("SELECT COUNT(*) FROM seen").fetchone()
    return int(n)
