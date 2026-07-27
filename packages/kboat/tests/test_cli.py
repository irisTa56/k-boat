"""Tests for the shared `kboat-*` CLI plumbing.

Only `--today`'s default is covered here; the rest of `kboat.cli` is exercised
through the console scripts that use it, which pass every flag explicitly.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Iterator

import pytest

from kboat.cli import add_today_argument

# Two zones 25 hours apart, so their calendar dates never agree — at every instant
# one of them has already turned over. That is what makes the assertion below able
# to fail: a `--today` computed in UTC (or in any single fixed zone) would answer
# the same string under both, and no time of day can make that right.
_UTC_PLUS_14 = "Pacific/Kiritimati"
_UTC_MINUS_11 = "Pacific/Midway"


@pytest.fixture
def in_zone(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Run a test under a chosen timezone, and put the process back afterwards.

    The explicit `undo` is what makes the restore complete: the C library keeps its own
    cached zone until `tzset` runs, so putting `TZ` back has to be followed by a
    `tzset` in the same finalizer.
    """

    def use(zone: str) -> None:
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    yield use

    monkeypatch.undo()
    time.tzset()


def _default_today() -> str:
    parser = argparse.ArgumentParser()
    add_today_argument(parser)
    return str(parser.parse_args([]).today)


@pytest.mark.parametrize("zone", [_UTC_PLUS_14, _UTC_MINUS_11])
def test_today_defaults_to_the_local_calendar_day(
    zone: str, in_zone: Callable[[str], None]
) -> None:
    # The oracle is `strftime`, not the expression under test: the default has to be
    # the day the reader is having, whatever the machine's zone.
    in_zone(zone)
    assert _default_today() == time.strftime("%Y-%m-%d")


def test_today_tracks_the_zone_rather_than_utc(in_zone: Callable[[str], None]) -> None:
    # The regression this guards: DTZ011 pushes toward `datetime.now(UTC).date()`,
    # which is lint-clean, one token shorter, and stamps the wrong day for most of
    # a JST morning. Under these two zones a UTC answer is identical; a local one
    # cannot be. It also catches a runner with no tzdata, where both zones silently
    # fall back to UTC and the test above would pass while proving nothing.
    in_zone(_UTC_PLUS_14)
    ahead = _default_today()
    in_zone(_UTC_MINUS_11)

    assert ahead != _default_today()
