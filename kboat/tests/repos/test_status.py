"""Tests for the `status` activity buckets."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kboat.repos.status import derive_status

TODAY = date(2026, 6, 6)


def _days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def test_archived_wins_over_recency() -> None:
    assert derive_status(True, _days_ago(1), today=TODAY) == "archived"


def test_no_timestamp_is_unknown() -> None:
    assert derive_status(False, None, today=TODAY) == "unknown"
    assert derive_status(False, "", today=TODAY) == "unknown"


@pytest.mark.parametrize(
    "days, expected",
    [
        (0, "recent"),
        (60, "recent"),
        (61, "active"),
        (180, "active"),
        (181, "slow"),
        (730, "slow"),
        (731, "dormant"),
        (2000, "dormant"),
    ],
)
def test_buckets(days: int, expected: str) -> None:
    assert derive_status(False, _days_ago(days), today=TODAY) == expected


def test_accepts_full_iso_timestamp() -> None:
    assert derive_status(False, "2026-06-05T12:34:56Z", today=TODAY) == "recent"
