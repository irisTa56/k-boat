"""Forum-adapter state store — post-grain dedupe and watch/poll throttle.

This module owns all SQL queries against the ``forum_watch`` and
``forum_post_seen`` tables that are created by the v2 migration in
``seen.py`` (FRM-GUD-004).  It is the post-grain dedupe authority for forum
sources (FRM-CON-001 / FRM-CON-005) and the scheduling oracle for the
sparse-poll watch set (FRM-007).

Design constraints honoured here:
- FRM-CON-001: no existing ``seen`` table or non-forum paths are touched.
- FRM-CON-005: ``admit_topic`` is the only idempotent write in the
  ``forum-new`` path; ``forum_post_seen`` and ``completed_polls`` are written
  only by ``record_post`` / ``finalize_poll`` (the record path).
- FRM-CON-004: ``last_like_count`` enables a short-circuit that skips a JSON
  fetch when the topic-level count is unchanged since the last poll.
- FRM-007: ``due_topics`` / ``finalize_poll`` implement the offset schedule;
  retirement is offset-only (``completed_polls >= len(offsets)``).
- FRM-PAT-002: time is injected at the seams that drive scheduling —
  ``first_seen_at`` into ``admit_topic`` and ``now`` into ``due_topics`` — so
  the poll schedule is unit-tested without real time. ``record_post`` instead
  stamps ``seen_at`` from the system clock (mirroring ``seen.record``); that
  timestamp is audit-only and feeds no scheduling decision, so it needs no seam.

The caller must supply a ``sqlite3.Connection`` opened via ``seen.open_db``
(the forum tables are in the same file).  All public functions commit on
success and use parameterized SQL (no f-string value interpolation).
"""

from __future__ import annotations

import sqlite3
import time
from typing import TypedDict

# ---------------------------------------------------------------------------
# Admission (TASK-012)
# ---------------------------------------------------------------------------


def admit_topic(
    conn: sqlite3.Connection,
    site_id: str,
    topic_id: int,
    first_seen_at: int,
) -> None:
    """Idempotently admit a topic into the watch set (FRM-CON-005).

    INSERT-OR-IGNORE so a second call for the same ``(site_id, topic_id)``
    is a silent no-op — it never resets ``first_seen_at`` or
    ``completed_polls``.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO forum_watch
            (site_id, topic_id, first_seen_at, op_interest_kept,
             completed_polls, last_like_count, retired)
        VALUES (?, ?, ?, NULL, 0, NULL, 0)
        """,
        (site_id, topic_id, first_seen_at),
    )
    conn.commit()


def set_op_verdict(
    conn: sqlite3.Connection,
    site_id: str,
    topic_id: int,
    kept: int,
) -> None:
    """Record the Rule-A OP verdict for a topic (FRM-002).

    ``kept`` should be ``1`` (reminded) or ``0`` (dropped).
    """
    conn.execute(
        """
        UPDATE forum_watch
        SET op_interest_kept = ?
        WHERE site_id = ? AND topic_id = ?
        """,
        (kept, site_id, topic_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Post dedupe (TASK-013)
# ---------------------------------------------------------------------------


def is_post_seen(conn: sqlite3.Connection, site_id: str, post_id: int) -> bool:
    """True iff ``post_id`` has been dispositioned for ``site_id`` (FRM-006)."""
    row = conn.execute(
        "SELECT 1 FROM forum_post_seen WHERE site_id = ? AND post_id = ?",
        (site_id, post_id),
    ).fetchone()
    return row is not None


def record_post(
    conn: sqlite3.Connection,
    site_id: str,
    topic_id: int,
    post_id: int,
    kept: int,
) -> None:
    """Upsert a dispositioned post into ``forum_post_seen`` (FRM-CON-005).

    Re-recording the same post updates ``kept`` and ``seen_at``; it does not
    insert a duplicate row.  ``kept`` should be ``1`` (reminded) or ``0``
    (dropped).
    """
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO forum_post_seen (site_id, post_id, topic_id, kept, seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(site_id, post_id) DO UPDATE SET
            topic_id = excluded.topic_id,
            kept     = excluded.kept,
            seen_at  = excluded.seen_at
        """,
        (site_id, post_id, topic_id, kept, now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Scheduling (TASK-014)
# ---------------------------------------------------------------------------


class WatchRow(TypedDict):
    """A single row returned by ``due_topics``.

    Contains the fields the Rule-B pipeline needs to assemble candidates and
    to resolve the effective like threshold (FRM-003 / FRM-CON-004).
    """

    topic_id: int
    first_seen_at: int
    completed_polls: int
    op_interest_kept: int | None
    last_like_count: int | None


def due_topics(
    conn: sqlite3.Connection,
    site_id: str,
    offsets: tuple[int, ...] | list[int],
    now: int,
) -> list[WatchRow]:
    """Return topics whose next poll offset has elapsed, ordered by
    ``first_seen_at`` then ``topic_id`` (deterministic and stable).

    A topic is due when it is not retired AND
    ``now - first_seen_at >= offsets[completed_polls] * 86400`` (FRM-007).

    "Due if elapsed, not a wall-clock appointment": a missed/offline run is
    caught up at the next run, and several elapsed offsets during downtime
    collapse into a single poll (only the current ``completed_polls`` index is
    checked — the topic becomes due once and stays due until
    ``finalize_poll`` advances the counter).

    Returns an empty list when ``offsets`` is empty.
    """
    if not offsets:
        return []

    rows: list[WatchRow] = []
    all_rows = conn.execute(
        """
        SELECT topic_id, first_seen_at, completed_polls,
               op_interest_kept, last_like_count
        FROM forum_watch
        WHERE site_id = ? AND retired = 0
        ORDER BY first_seen_at ASC, topic_id ASC
        """,
        (site_id,),
    ).fetchall()

    for topic_id, first_seen_at, completed_polls, op_interest_kept, last_like_count in all_rows:
        # Guard the offsets[] index below. In normal operation a non-retired row
        # always has completed_polls < len(offsets): the same finalize_poll that
        # reaches the bound also sets retired=1, excluded by the SQL filter
        # above. This arm fires only if poll_offsets_days is later *shortened* in
        # config — without it, offsets[completed_polls] would raise IndexError.
        if completed_polls >= len(offsets):
            continue
        threshold_seconds = offsets[completed_polls] * 86400
        if now - first_seen_at >= threshold_seconds:
            rows.append(
                WatchRow(
                    topic_id=topic_id,
                    first_seen_at=first_seen_at,
                    completed_polls=completed_polls,
                    op_interest_kept=op_interest_kept,
                    last_like_count=last_like_count,
                )
            )
    return rows


def finalize_poll(
    conn: sqlite3.Connection,
    site_id: str,
    topic_id: int,
    like_count: int,
    offsets: tuple[int, ...] | list[int],
) -> None:
    """Advance the poll counter for a topic after all its posts are dispositioned.

    Increments ``completed_polls``, stores the current ``like_count`` (for the
    FRM-CON-004 short-circuit), and sets ``retired = 1`` when
    ``completed_polls >= len(offsets)`` (offset-only retirement, FRM-007).

    Takes no ``now``: the poll schedule is anchored to ``first_seen_at``, so
    advancing the counter needs no current time, and the table has no
    last-polled timestamp to stamp (retirement is offset-only, FRM-007).

    Must be the **last** call for a topic in a run, after every candidate post
    has been dispositioned (FRM-CON-005 / FRM-PAT-001).
    """
    # Fetch current completed_polls to compute the new value.
    row = conn.execute(
        "SELECT completed_polls FROM forum_watch WHERE site_id = ? AND topic_id = ?",
        (site_id, topic_id),
    ).fetchone()
    if row is None:
        return  # Topic not in watch set; nothing to advance.

    new_polls = row[0] + 1
    retired = 1 if new_polls >= len(offsets) else 0

    conn.execute(
        """
        UPDATE forum_watch
        SET completed_polls = ?,
            last_like_count = ?,
            retired         = ?
        WHERE site_id = ? AND topic_id = ?
        """,
        (new_polls, like_count, retired, site_id, topic_id),
    )
    conn.commit()


def last_like_count(
    conn: sqlite3.Connection,
    site_id: str,
    topic_id: int,
) -> int | None:
    """Return the stored topic-level like count, or ``None`` if unset.

    Used by the Rule-B pipeline for the FRM-CON-004 short-circuit: skip the
    JSON fetch when ``completed_polls > 0`` and this value is unchanged.
    Returns ``None`` on the first poll (``last_like_count`` is ``NULL`` until
    ``finalize_poll`` runs at least once).
    """
    row = conn.execute(
        "SELECT last_like_count FROM forum_watch WHERE site_id = ? AND topic_id = ?",
        (site_id, topic_id),
    ).fetchone()
    if row is None:
        return None
    return row[0]  # may be None if last_like_count column is NULL
