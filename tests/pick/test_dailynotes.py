"""Tests for recent Daily-note body extraction."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from kboat.pick.dailynotes import extract_daily_notes


def _write(daily: Path, name: str, text: str) -> None:
    daily.mkdir(parents=True, exist_ok=True)
    (daily / name).write_text(text, encoding="utf-8")


def test_newest_first_and_skips_empty_body(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    _write(daily, "2026-06-02.md", "wondering about KV cache tricks\n")
    _write(daily, "2026-06-04.md", "- read up on distributed training\n- and agents\n")
    # A note with only frontmatter (no body) carries no signal and is skipped.
    _write(daily, "2026-06-03.md", "---\ntags: [daily]\n---\n\n   \n")
    out = extract_daily_notes(daily, date(2026, 6, 12))
    assert [dn.date for dn in out] == ["2026-06-04", "2026-06-02"]
    assert "distributed training" in out[0].body
    assert out[1].body == "wondering about KV cache tricks"


def test_frontmatter_is_stripped(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    _write(
        daily,
        "2026-06-04.md",
        "---\ntags: [daily]\nmood: ok\n---\n\n## Notes\n- curious about RAG\n",
    )
    out = extract_daily_notes(daily, date(2026, 6, 12))
    assert out[0].body == "## Notes\n- curious about RAG"
    assert "tags:" not in out[0].body


def test_body_without_frontmatter_kept_whole(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    _write(daily, "2026-06-04.md", "just a plain line about geospatial indexing\n")
    out = extract_daily_notes(daily, date(2026, 6, 12))
    assert out[0].body == "just a plain line about geospatial indexing"


def test_unclosed_frontmatter_is_not_stripped(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    # A leading `---` with no closing fence is not frontmatter; keep the text whole.
    _write(daily, "2026-06-04.md", "---\nstray dashes, then prose\n")
    out = extract_daily_notes(daily, date(2026, 6, 12))
    assert out[0].body == "---\nstray dashes, then prose"


def test_future_dated_and_non_date_files_ignored(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    _write(daily, "2026-06-20.md", "from the future\n")
    _write(daily, "scratch.md", "not a daily note\n")
    _write(daily, "2026-06-04.md", "valid\n")
    out = extract_daily_notes(daily, date(2026, 6, 12))
    assert [dn.date for dn in out] == ["2026-06-04"]


def test_missing_daily_dir_is_empty(tmp_path: Path) -> None:
    assert extract_daily_notes(tmp_path / "Daily", date(2026, 6, 12)) == []


def test_lookback_window_excludes_stale_notes(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    # today = 2026-06-13, default 14-day window → earliest in scope is 2026-05-30.
    _write(daily, "2026-05-30.md", "exactly at the boundary\n")
    _write(daily, "2026-05-29.md", "one day too old\n")
    _write(daily, "2026-06-13.md", "today\n")
    out = extract_daily_notes(daily, date(2026, 6, 13))
    assert [dn.date for dn in out] == ["2026-06-13", "2026-05-30"]


def test_lookback_window_is_configurable(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    _write(daily, "2026-06-04.md", "eight days back\n")
    _write(daily, "2026-06-10.md", "two days back\n")
    # A 3-day window keeps only 2026-06-10 (2026-06-04 is out of scope).
    out = extract_daily_notes(daily, date(2026, 6, 12), lookback_days=3)
    assert [dn.date for dn in out] == ["2026-06-10"]


def test_lookback_zero_is_today_only(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    # earliest == today, so only today's note is in scope (inclusive boundary).
    _write(daily, "2026-06-12.md", "today\n")
    _write(daily, "2026-06-11.md", "yesterday\n")
    out = extract_daily_notes(daily, date(2026, 6, 12), lookback_days=0)
    assert [dn.date for dn in out] == ["2026-06-12"]
