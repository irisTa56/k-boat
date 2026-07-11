"""Behavior tests for the transient feed-body cache (``body_cache``).

The cache is exercised through its public API against a real (tmp) seen-store DB,
so the v5 ``entry_body`` migration and the SQL are covered end to end.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from feed_filter import body_cache
from feed_filter.seen import open_db


def test_replace_then_get_roundtrips(tmp_path: Path) -> None:
    with contextlib.closing(open_db(tmp_path / "t.db")) as conn:
        body_cache.replace(conn, [("https://e/1", "body one"), ("https://e/2", "body two")])
        assert body_cache.get(conn, "https://e/1") == "body one"
        assert body_cache.get(conn, "https://e/2") == "body two"


def test_get_miss_returns_none(tmp_path: Path) -> None:
    with contextlib.closing(open_db(tmp_path / "t.db")) as conn:
        assert body_cache.get(conn, "https://e/absent") is None


def test_replace_evicts_the_prior_run(tmp_path: Path) -> None:
    # ``replace`` rewrites the cache wholesale, so a body from an earlier run is
    # gone — the cache holds at most one run's entries.
    with contextlib.closing(open_db(tmp_path / "t.db")) as conn:
        body_cache.replace(conn, [("https://e/old", "old")])
        body_cache.replace(conn, [("https://e/new", "new")])
        assert body_cache.get(conn, "https://e/old") is None
        assert body_cache.get(conn, "https://e/new") == "new"


def test_replace_keeps_the_longest_body_on_duplicate_key(tmp_path: Path) -> None:
    # One run's emitted set can carry the same canonical_url twice (the same article
    # in two feeds). replace must not raise on the PRIMARY KEY, and it keeps the
    # richer (longer) body regardless of insert order.
    with contextlib.closing(open_db(tmp_path / "t.db")) as conn:
        body_cache.replace(conn, [("https://e/dup", "short"), ("https://e/dup", "a longer body")])
        assert body_cache.get(conn, "https://e/dup") == "a longer body"
        # Reversed order: the longer body still wins (order-independent).
        body_cache.replace(conn, [("https://e/dup", "a longer body"), ("https://e/dup", "short")])
        assert body_cache.get(conn, "https://e/dup") == "a longer body"


def test_replace_empty_clears(tmp_path: Path) -> None:
    with contextlib.closing(open_db(tmp_path / "t.db")) as conn:
        body_cache.replace(conn, [("https://e/x", "x")])
        body_cache.replace(conn, [])
        assert body_cache.get(conn, "https://e/x") is None
