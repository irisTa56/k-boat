"""Transient per-run cache of full feed bodies (SH-sibling; keeps bodies out of
the run orchestrator's context).

``new-entries`` writes each emitted feed entry's full body here and puts only a
short preview on stdout; the judging haiku subagent pulls the full body on demand
via the ``entry-body`` subcommand, so the body lands only in the cheap judge's
context, never the orchestrator's. The cache is rewritten wholesale each
``new-entries`` run (``replace``), so it holds at most one run's bodies and never
grows unbounded.

It is **operational cache, not dedupe / never-lost state**: a miss — a non-feed
entry, an interrupted run, or a row evicted by a later ``new-entries`` — returns
``None`` and falls the judge back to a full-page WebFetch, costing at
most a redundant fetch, never a lost entry. The table (``entry_body``, v5 migration
in ``seen.py``) is keyed by the emitted canonical-URL string, so a lookup matches
exactly what ``new-entries`` put on stdout.
"""

from __future__ import annotations

import sqlite3


def replace(conn: sqlite3.Connection, items: list[tuple[str, str]]) -> None:
    """Atomically clear the cache and store ``items`` (canonical URL → full body).

    Clearing first bounds the cache to one run's entries; the delete and the
    inserts share the implicit transaction the ``commit`` closes, so a reader
    never sees a half-populated cache. An empty ``items`` leaves the cache cleared.

    The upsert (not a bare ``INSERT``) is required because one run's emitted set can
    legitimately carry the same ``canonical_url`` twice — two subscribed feeds
    running the same article, or two items that canonicalize alike — and nothing is
    recorded seen during ``new-entries`` to collapse them; a bare INSERT would raise
    on the PRIMARY KEY and wedge the whole run (never-lost). On a collision
    the **longer** body is kept (``length(excluded.body) > length(...)``), not the
    last writer's: the two are the same article, and this feature exists to judge
    from the full body, so keeping the richer one (e.g. a full ``content:encoded``
    over a sibling feed's truncated ``<description>``) is order-independent and
    strictly better.
    """
    conn.execute("DELETE FROM entry_body")
    conn.executemany(
        """
        INSERT INTO entry_body (canonical_url, body) VALUES (?, ?)
        ON CONFLICT(canonical_url) DO UPDATE SET body = excluded.body
            WHERE length(excluded.body) > length(entry_body.body)
        """,
        items,
    )
    conn.commit()


def get(conn: sqlite3.Connection, url: str) -> str | None:
    """The cached full body for ``url``, or ``None`` on a miss."""
    row = conn.execute("SELECT body FROM entry_body WHERE canonical_url = ?", (url,)).fetchone()
    return row[0] if row is not None else None
