"""The seen-store — the sole dedupe authority.

A single SQLite table keyed by canonical URL. A row exists iff the URL has been
processed (kept, dropped, or snapshotted at registration). ``kept`` distinguishes
the three: ``1`` reminded, ``0`` dropped, ``NULL`` snapshotted-only (the
cold-start flood guard). Membership — not ``kept`` — is what
``is_seen`` tests.

Keys are typed ``CanonicalUrl`` so a caller that forgets to canonicalize is a
``ty`` error, not a silent duplicate note.
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
# database from before this migration framework existed.
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
    # v2: forum adapter tables.
    # These are the Discourse adapter's post-grain dedupe authority and
    # watch/poll throttle.  They are colocated here only because migrations
    # share one ``user_version`` counter; all queries against them live in
    # ``forum_store.py``, which keeps ``seen.py`` the sole authority for the
    # ``seen`` table.
    #
    # ``forum_watch``: one row per admitted topic, anchoring the poll schedule.
    # ``op_interest_kept`` and ``last_like_count`` are nullable so
    # "unset" (not yet judged / not yet polled) is distinguishable from 0.
    # ``completed_polls`` counts finalized polls;
    # ``retired`` is set when ``completed_polls >= len(poll_offsets_days)``
    # (offset-only retirement).
    #
    # ``forum_post_seen``: one row per dispositioned post (kept or dropped),
    # enforcing post-grain dedupe.
    """
    CREATE TABLE IF NOT EXISTS forum_watch (
        site_id          TEXT    NOT NULL,
        topic_id         INTEGER NOT NULL,
        first_seen_at    INTEGER NOT NULL,
        op_interest_kept INTEGER,
        completed_polls  INTEGER NOT NULL DEFAULT 0,
        last_like_count  INTEGER,
        retired          INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (site_id, topic_id)
    );

    CREATE TABLE IF NOT EXISTS forum_post_seen (
        site_id  TEXT    NOT NULL,
        post_id  INTEGER NOT NULL,
        topic_id INTEGER NOT NULL,
        kept     INTEGER NOT NULL,
        seen_at  INTEGER NOT NULL,
        PRIMARY KEY (site_id, post_id)
    );
    """,
    # v3: bound the Rule-B poll set to top-feed topics.
    # ``poll_eligible`` marks a watched topic as one to JSON-poll for Rule B.
    # Only topics surfaced by ``top.rss`` (daily/weekly) are poll-eligible; a
    # ``latest.rss``-only topic is still admitted (so its Rule-A OP verdict is
    # tracked once) but is never JSON-polled, so the per-run poll sweep is bounded
    # to the top-N instead of every latest topic — the prior behavior tripped the
    # forum's anonymous rate limit (429). Existing rows default to 0 (not polled)
    # and re-upgrade to 1 on their next top-feed admission (one-directional, see
    # ``forum_store.admit_topic``).
    """
    ALTER TABLE forum_watch ADD COLUMN poll_eligible INTEGER NOT NULL DEFAULT 0;
    """,
    # v4: per-site consecutive-failure counter for gather-error escalation.
    # One row per site; ``consecutive_failures`` counts runs where
    # the site was genuinely unreachable, reset to 0 on any reachable run.
    #
    # This is **operational telemetry, not dedupe / never-lost state**:
    # it is explicitly outside the never-lost authority
    # that governs ``forum_post_seen`` / ``forum_watch`` / ``completed_polls``. A
    # crash after this write costs at most one extra increment on a failure that
    # would recur anyway — never a lost post. All queries against it live in
    # ``site_health.py``, keeping ``seen.py`` the authority only for
    # the ``seen`` table. A ``last_error`` / ``last_failed_at`` column is
    # deferred until a consumer exists; append-only migrations make adding one
    # later a one-line change (the current error is already emitted in
    # ``sites[].error`` every run).
    """
    CREATE TABLE IF NOT EXISTS site_health (
        site_id              TEXT    PRIMARY KEY,
        consecutive_failures INTEGER NOT NULL DEFAULT 0
    );
    """,
    # v5: transient per-run cache of full feed bodies. ``new-entries`` stores each
    # emitted feed entry's full body here and puts only a short preview on stdout,
    # so the body reaches only the judging haiku (via the ``entry-body`` subcommand),
    # never the run orchestrator's context. Rewritten wholesale each
    # ``new-entries`` run, so it holds at most one run's bodies.
    #
    # Like ``site_health``, this is **operational cache, not dedupe / never-lost
    # state**: a miss (interrupted run, or a row evicted by a later ``new-entries``)
    # simply falls the judge back to a full-page WebFetch, costing at most a
    # redundant fetch — never a lost entry. All queries live in ``body_cache.py``,
    # keeping ``seen.py`` the authority only for the ``seen`` table.
    """
    CREATE TABLE IF NOT EXISTS entry_body (
        canonical_url TEXT PRIMARY KEY,
        body          TEXT NOT NULL
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
    """Bulk-mark ``urls`` as seen with ``kept=NULL`` and no notes.

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
