"""Site-health store — the durable per-site consecutive-failure counter (SH-REQ-001).

This module owns all SQL against the ``site_health`` table created by the v4
migration in ``seen.py`` (SH-GUD-001), mirroring how ``forum_store`` owns the
forum-table SQL and keeps ``seen.py`` the authority only for the ``seen`` table.

The counter is the sole cross-run signal that lets a stateless run distinguish a
persistent site failure from a one-run blip: each scheduled run absorbs a gather
error and reports it, but without this durable count it has no memory of prior
runs, so it re-judges a recurring failure as "transient" every time and never
escalates (the 2026-07-10 session review found exactly this — see the plan at
``plan/feature-site-health-escalation-1.md``).

The table is **operational telemetry, not dedupe / never-lost state**
(SH-CON-002): it is carved out of the never-lost authority (FRM-CON-005). A crash
after a write costs at most one extra increment on a failure that would recur
anyway — never a lost post. The store is source-kind-agnostic (keyed by
``site_id``), so both the forum path (``cmd_forum_new``) and the article path
(``cmd_new_entries``) share it verbatim (SH-GUD-002).

The caller must supply a ``sqlite3.Connection`` opened via ``seen.open_db`` (the
``site_health`` table is in the same file). Writes commit on success and use
parameterized SQL, no f-string value interpolation (SH-GUD-003).
"""

from __future__ import annotations

import sqlite3


def record_failure(conn: sqlite3.Connection, site_id: str) -> int:
    """Increment ``site_id``'s consecutive-failure counter; return the new count.

    Upsert: a first failure inserts the row at 1, a repeat failure increments the
    existing count. Commits on success (SH-GUD-003). ``RETURNING`` reads back the
    post-write count in the same statement (SQLite ≥ 3.35), so the caller gets the
    value it emits as ``consecutive_failures`` and feeds to ``is_persistent``
    without a second round-trip (SH-REQ-002).
    """
    (count,) = conn.execute(
        """
        INSERT INTO site_health (site_id, consecutive_failures)
        VALUES (?, 1)
        ON CONFLICT(site_id) DO UPDATE SET
            consecutive_failures = consecutive_failures + 1
        RETURNING consecutive_failures
        """,
        (site_id,),
    ).fetchone()
    conn.commit()
    return int(count)


def record_success(conn: sqlite3.Connection, site_id: str) -> None:
    """Reset ``site_id``'s consecutive-failure counter to 0; commit on success.

    A reachable run clears the streak (SH-REQ-002). No-op-safe when the site has
    no row yet (it has never failed): the ``UPDATE`` matches nothing and there is
    nothing to reset, so no row is inserted — an unfailed site stays absent rather
    than accumulating a zero row.
    """
    conn.execute(
        "UPDATE site_health SET consecutive_failures = 0 WHERE site_id = ?",
        (site_id,),
    )
    conn.commit()


def is_persistent(count: int, threshold: int) -> bool:
    """True iff ``count`` consecutive failures has reached ``threshold`` (SH-REQ-004).

    Pure helper so the persistence decision is made once in Python and the skill
    never re-derives the threshold (SH-REQ-005). The boundary is inclusive: a site
    is persistent at exactly ``threshold`` failures, not one past it.
    """
    return count >= threshold
