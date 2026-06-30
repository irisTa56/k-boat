"""Tests for active-web candidate selection."""

from __future__ import annotations

from kboat.pick.candidates import candidate_from, is_active_web
from kboat.pick.notes import Value


def _fm(**over: Value) -> dict[str, Value]:
    base: dict[str, Value] = {
        "type": "source",
        "source_type": "web_page",
        "reading": False,
        "distill": False,
        "keep": False,
        "dismiss": False,
        "blocked": False,
    }
    base.update(over)
    return base


def test_active_web_accepts_undispositioned_web() -> None:
    assert is_active_web(_fm()) is True


def test_rejects_pdf_and_non_source() -> None:
    assert is_active_web(_fm(source_type="pdf")) is False
    assert is_active_web(_fm(type="kindle")) is False


def test_rejects_any_disposition_or_blocked() -> None:
    for flag in ("distill", "keep", "dismiss", "blocked"):
        assert is_active_web(_fm(**{flag: True})) is False


def test_rejects_in_progress_reading() -> None:
    # An in-progress read already shows in the Today view via `reading`, so it is
    # excluded from the pick candidate set (strict subset of the Web inbox view).
    assert is_active_web(_fm(reading=True)) is False


def test_candidate_extraction_defaults_missing_fields() -> None:
    fm = _fm(
        title="T",
        url="https://x",
        reading_link="https://x",
        summary="S",
        topics=["a", "b"],
        added_date="2026-06-01",
        notebooklm_id="nb-1",
    )
    c = candidate_from("slug1", "Sources/slug1.md", fm)
    assert c.to_json() == {
        "slug": "slug1",
        "path": "Sources/slug1.md",
        "title": "T",
        "url": "https://x",
        "reading_link": "https://x",
        "summary": "S",
        "topics": ["a", "b"],
        "added_date": "2026-06-01",  # the diversification key (one older + one newer)
        "notebooklm_id": "nb-1",  # Stage 2 resolves the fulltext through this notebook
    }
    # Missing optional fields coerce to empty, never None, so the JSON is stable. An
    # empty notebooklm_id (notebook gone) is the ranker's "un-fetchable" signal.
    bare = candidate_from("s", "Sources/s.md", _fm())
    assert bare.summary == "" and bare.topics == [] and bare.added_date == ""
    assert bare.notebooklm_id == ""
