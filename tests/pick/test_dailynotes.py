"""Tests for Daily-note question extraction."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from kboat.pick.dailynotes import extract_questions


def _write(daily: Path, name: str, text: str) -> None:
    daily.mkdir(parents=True, exist_ok=True)
    (daily / name).write_text(text, encoding="utf-8")


def test_newest_first_and_skips_empty(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    _write(daily, "2026-06-02.md", "## 明日への問い\n\n- old question\n")
    _write(daily, "2026-06-04.md", "## 明日への問い\n\n- newer one\n- and another\n")
    # A note with only the seeded empty bullet contributes nothing.
    _write(daily, "2026-06-03.md", "## 明日への問い\n\n-\n")
    out = extract_questions(daily, date(2026, 6, 12))
    assert [dq.date for dq in out] == ["2026-06-04", "2026-06-02"]
    assert out[0].items == ["newer one", "and another"]
    assert out[1].items == ["old question"]


def test_section_stops_at_next_heading(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    _write(daily, "2026-06-04.md", "## 明日への問い\n- keep me\n\n## メモ\n- not a question\n")
    out = extract_questions(daily, date(2026, 6, 12))
    assert out[0].items == ["keep me"]


def test_future_dated_and_non_date_files_ignored(tmp_path: Path) -> None:
    daily = tmp_path / "Daily"
    _write(daily, "2026-06-20.md", "## 明日への問い\n- from the future\n")
    _write(daily, "scratch.md", "## 明日への問い\n- not a daily note\n")
    _write(daily, "2026-06-04.md", "## 明日への問い\n- valid\n")
    out = extract_questions(daily, date(2026, 6, 12))
    assert [dq.date for dq in out] == ["2026-06-04"]


def test_missing_daily_dir_is_empty(tmp_path: Path) -> None:
    assert extract_questions(tmp_path / "Daily", date(2026, 6, 12)) == []
