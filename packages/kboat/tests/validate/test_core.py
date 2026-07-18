"""Tests for the per-field and cross-field note checks."""

from __future__ import annotations

from kboat.frontmatter import Value
from kboat.validate.core import check_note


def _source(**over: Value) -> dict[str, Value]:
    fm: dict[str, Value] = {
        "type": "source",
        "title": "T",
        "reading_link": "https://x",
        "gemini_url": "https://g",
        "reading": False,
        "distill": False,
        "keep": False,
        "dismiss": False,
        "source_type": "web_page",
        "url": "https://x",
        "summary": "s",
        "topics": ["a"],
        "added_date": "2026-06-01",
        "filed_date": None,
        "distilled_date": None,
        "blocked": False,
        "picked": False,
        "notebooklm_id": "id",
        "notebooklm_url": "https://n",
        "tags": [],
    }
    fm.update(over)
    return fm


def _codes(note_type: str, fm: dict[str, Value]) -> set[str]:
    return {v.code for v in check_note(note_type, fm, "p")}


def test_valid_source_is_clean() -> None:
    assert check_note("source", _source(), "p") == []


def test_pdf_source_with_null_url_is_clean() -> None:
    assert check_note("source", _source(source_type="pdf", url=None), "p") == []


def test_empty_required_bool() -> None:
    assert "empty_required" in _codes("source", _source(reading=None))


def test_non_bool_value() -> None:
    assert "not_bool" in _codes("source", _source(distill="yes"))


def test_bad_enum_and_date() -> None:
    assert "bad_enum" in _codes("source", _source(source_type="audio"))
    assert "bad_date" in _codes("source", _source(added_date="2026/06/01"))


def test_block_list_must_be_a_list() -> None:
    assert "not_list" in _codes("source", _source(topics="notalist"))


def test_missing_field() -> None:
    fm = _source()
    del fm["title"]
    assert "missing_field" in _codes("source", fm)


def test_absent_optional_notebook_field_is_ok() -> None:
    fm = _source()
    del fm["notebooklm_id"]  # present=False — absence is allowed
    assert check_note("source", fm, "p") == []


def test_ambiguous_dispositions() -> None:
    assert "ambiguous" in _codes("source", _source(dismiss=True, keep=True))
    assert "ambiguous" in _codes("source", _source(dismiss=True, distill=True))
    # dismiss alone, or keep+distill, is fine.
    assert "ambiguous" not in _codes("source", _source(dismiss=True))
    assert "ambiguous" not in _codes("source", _source(keep=True, distill=True))


def test_blocked_must_have_no_notebook() -> None:
    assert "blocked_has_notebook" in _codes("source", _source(blocked=True, notebooklm_id="id"))
    assert "blocked_has_notebook" not in _codes("source", _source(blocked=True, notebooklm_id=None))
    # A discarded notebook leaves `notebooklm_id: ""` (a blank string, not None);
    # that is the empty/tombstone state, so a blocked source carrying it is clean.
    assert "blocked_has_notebook" not in _codes("source", _source(blocked=True, notebooklm_id=""))


def test_picked_is_web_only() -> None:
    assert "picked_non_web" in _codes("source", _source(picked=True, source_type="pdf", url=None))
    assert "picked_non_web" not in _codes("source", _source(picked=True))


def test_web_page_needs_a_url() -> None:
    assert "web_missing_url" in _codes("source", _source(url=None))
    # A blank url is as missing as a null one for a web page.
    assert "web_missing_url" in _codes("source", _source(url="  "))


def test_blank_string_in_required_field_is_empty() -> None:
    # A required scalar hand-edited to a blank string is empty, not a valid value.
    assert "empty_required" in _codes("source", _source(title=""))
    assert "empty_required" in _codes("source", _source(source_type="  "))


def test_valid_kindle_and_repo() -> None:
    kindle: dict[str, Value] = {
        "type": "kindle",
        "title": "Book",
        "reading_link": "https://read.amazon.co.jp/?asin=B0",
        "author": ["A"],
        "store_link": "https://www.amazon.co.jp/dp/B0",
        "published": "2021",
        "publisher": "Pub",
        "reading": True,
        "finished": False,
        "distill": False,
        "distilled_date": None,
        "added_date": "2026-05-12",
        "tags": ["kindle"],
    }
    assert check_note("kindle", kindle, "p") == []

    repo: dict[str, Value] = {
        "type": "repo",
        "title": "owner/repo",
        "url": "https://github.com/owner/repo",
        "homepage": "",
        "reading": False,
        "description": "desc",
        "language": "[Python]",  # inline list parses as raw string
        "topics": [],
        "stars": "42",
        "archived": False,
        "created_at": "2020-01-01",
        "last_commit": "2026-06-01",
        "license": "MIT",
        "role": "library",
        "domain": "[tools]",
        "summary": "a repo",
        "status": "recent",
        "added_date": "2026-06-01",
        "refreshed_date": "2026-06-10",
    }
    assert check_note("repo", repo, "p") == []
    assert "bad_enum" in _codes("repo", {**repo, "status": "ancient"})
    assert "not_int" in _codes("repo", {**repo, "stars": "lots"})
    assert "bad_date" in _codes("repo", {**repo, "last_commit": "2026-06-01T00:00:00Z"})

    # Repo cross-field: status must agree with the archived flag.
    assert "status_archived_mismatch" in _codes("repo", {**repo, "archived": True})
    assert "status_archived_mismatch" in _codes("repo", {**repo, "status": "archived"})
    assert check_note("repo", {**repo, "archived": True, "status": "archived"}, "p") == []

    # Missing a required field is caught for each note type.
    assert "missing_field" in _codes("repo", {k: v for k, v in repo.items() if k != "role"})
    assert "missing_field" in _codes(
        "kindle", {k: v for k, v in kindle.items() if k != "store_link"}
    )
