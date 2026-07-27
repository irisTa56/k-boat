"""Tests for the per-field and cross-field note checks."""

from __future__ import annotations

import pytest

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


def test_an_inline_list_must_still_be_a_sequence() -> None:
    # An inline list reads back as its raw source, so a string is the only shape
    # a valid one has. That is not a licence for any string: the writer keeps a
    # wrong-typed value rather than erasing it, and this is where it shows.
    assert _codes("source", _source(tags="[a, b]")) == set()
    assert "not_list" in _codes("source", _source(tags="ai, agents"))
    assert "not_list" in _codes("source", _source(tags="[a: b]"))


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


# Two representatives; which strings are dates is `is_iso_date`'s own test.
@pytest.mark.parametrize("value", ["2026-02-29", "20260601"])
def test_a_date_field_holding_something_that_is_not_a_date_is_reported(value: str) -> None:
    # The validator and the lifecycle's ageing predicates share one definition of
    # a date, so anything the lifecycle cannot age is reported here as drift
    # rather than sitting in a note nothing can read.
    assert "bad_date" in _codes("source", _source(added_date=value))


def test_a_canonical_date_is_accepted() -> None:
    assert "bad_date" not in _codes("source", _source(added_date="2024-02-29"))


def test_distilled_date_requires_the_distill_flag() -> None:
    assert "distilled_without_distill" in _codes("source", _source(distilled_date="2026-06-01"))
    # With the flag set it is the ordinary terminal state.
    assert "distilled_without_distill" not in _codes(
        "source", _source(distill=True, distilled_date="2026-06-01")
    )
    # An empty date — bare, blank, or absent — says nothing was distilled.
    assert "distilled_without_distill" not in _codes("source", _source(distilled_date=None))
    assert "distilled_without_distill" not in _codes("source", _source(distilled_date="  "))
    fm = _source()
    del fm["distilled_date"]
    assert "distilled_without_distill" not in _codes("source", fm)


def test_a_kindle_note_is_held_to_the_same_distilled_rule() -> None:
    # Both distillable kinds, one check: the contradiction is identical, and the
    # asymmetry would be the kind of gap nothing else reports.
    kindle: dict[str, Value] = {"type": "kindle", "distill": False, "distilled_date": "2026-06-01"}
    assert "distilled_without_distill" in _codes("kindle", kindle)
    assert "distilled_without_distill" not in _codes("kindle", {**kindle, "distill": True})
    assert "distilled_without_distill" not in _codes("kindle", {**kindle, "distilled_date": None})


def test_a_disposition_without_a_filed_date_is_not_a_violation() -> None:
    # It is the state that triggers the routine's own Phase A stamp, so a rule
    # here would fire on every freshly-ticked checkbox. `kboat-validate --stats`
    # reports it as `awaiting_filed_stamp` instead.
    assert check_note("source", _source(distill=True, filed_date=None), "p") == []
    assert check_note("source", _source(keep=True, filed_date=None), "p") == []
    assert check_note("source", _source(dismiss=True, filed_date=None), "p") == []


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
    # What the writer had to quote is what this reports, and vice versa — one
    # predicate decides both, so no value is written as valid and read as not.
    for not_a_number in ("+42", "007", "٤٢", "4 2"):
        assert "not_int" in _codes("repo", {**repo, "stars": not_a_number}), not_a_number
    assert check_note("repo", {**repo, "stars": "-3"}, "p") == []
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


def _feed(**over: Value) -> dict[str, Value]:
    fm: dict[str, Value] = {
        "type": "feed",
        "title": "An article",
        "url": "https://example.com/post",
        "read": False,
        "shelved": False,
        "dismissed": False,
        "wall": False,
        "feed_kind": "article",
        "site_id": "example",
        "summary": "",
        "added_date": "2026-07-19",
    }
    fm.update(over)
    return fm


def test_valid_feed() -> None:
    assert check_note("feed", _feed(), "p") == []
    assert check_note("feed", _feed(feed_kind="forum", summary="a topic"), "p") == []
    # Two dispositions at once is not a schema violation, unlike a source's
    # exclusive ones. The feed booleans are independent: one-tick-per-exit is a
    # triage convention (kboat-feed-notes), and a stray extra tick is something
    # the reader sees and undoes in the Base, not vault drift worth reporting.
    assert check_note("feed", _feed(read=True, shelved=True, dismissed=True), "p") == []
    # The status booleans are always-present: a null one is empty_required.
    assert "empty_required" in _codes("feed", _feed(dismissed=None))
    assert "bad_enum" in _codes("feed", _feed(feed_kind="video"))
    assert "missing_field" in _codes("feed", {k: v for k, v in _feed().items() if k != "site_id"})
