"""Tests for `write_note` — the translation from a gather record to a `REPO` upsert.

The write contract itself (merge, stamps, body preservation, the collision
check) is `upsert`'s and is tested in `tests/test_upsert.py`; what is checked
here is that a repo record reaches it intact and that nothing the record carries
can reach a field the record does not own.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from kboat.frontmatter import parse_frontmatter
from kboat.naming import note_slug
from kboat.repos.gather import github_fields
from kboat.repos.write import write_note
from kboat.validate.core import check_note

# The writer verifies a record's slug against its `url`, so the fixture names the
# note the way `gather` does — through the one oracle.
URL = "https://github.com/google/A2A"
SLUG = note_slug(URL)

RECORD: dict[str, Any] = {
    "slug": SLUG,
    "url": URL,
    "title": "google/A2A",
    "fields": {
        "homepage": "https://a2a-protocol.org/",
        "description": "An open protocol: agents talk.",
        "language": ["Shell"],
        "topics": ["a2a", "agents"],
        "stars": 100,
        "archived": False,
        "created_at": "2025-03-25",
        "last_commit": "2026-05-01",
        "license": "apache-2.0",
        "status": "recent",
    },
    "role": "framework",
    "domain": ["ai-agents"],
    "summary": "エージェント間通信のプロトコル。",
    "readme_error": None,
}


def _note(vault: Path) -> Path:
    return vault / "Repos" / f"{SLUG}.md"


def test_write_creates_note(tmp_path: Path) -> None:
    result = write_note(RECORD, tmp_path, today_iso="2026-06-06")
    assert result["status"] == "created"
    note = _note(tmp_path).read_text()
    fm = parse_frontmatter(note)
    assert fm["type"] == "repo"
    assert fm["role"] == "framework"
    assert fm["status"] == "recent"
    assert fm["reading"] is False  # the always-present booleans the Bases filter on
    assert fm["gone"] is False
    assert fm["added_date"] == "2026-06-06"
    assert fm["refreshed_date"] == "2026-06-06"
    assert fm["stars"] == "100"  # the parser returns scalars as strings
    assert fm["archived"] is False
    assert 'description: "An open protocol: agents talk."' in note  # quoted, valid YAML
    assert "topics: [a2a, agents]" in note  # repo lists render inline, one line each
    assert "domain: [ai-agents]" in note
    assert note.rstrip().endswith("## Notes")  # somewhere to put notes, still empty


def test_a_record_whose_readme_fetch_failed_writes_a_note_marked_unavailable(
    tmp_path: Path,
) -> None:
    # The classification was made from the metadata alone, and the note is the
    # only place that survives the run, so the note has to say so.
    write_note(
        {**RECORD, "readme_error": "HTTP 403: rate limited"}, tmp_path, today_iso="2026-06-06"
    )

    note = _note(tmp_path).read_text()
    fm = parse_frontmatter(note)
    assert fm["readme"] == "unavailable"
    # The mark is the fact of the failure; `gh`'s message is untrusted text and
    # stays out of the vault.
    assert "rate limited" not in note
    assert check_note("repo", dict(fm), f"Repos/{SLUG}.md") == []


def test_a_record_whose_readme_was_fetched_writes_a_note_marked_fetched(tmp_path: Path) -> None:
    write_note(RECORD, tmp_path, today_iso="2026-06-06")

    assert parse_frontmatter(_note(tmp_path).read_text())["readme"] == "fetched"


def test_cataloguing_a_repo_again_marks_the_note_for_the_new_classification(
    tmp_path: Path,
) -> None:
    # A second write re-judges `role`/`domain`/`summary`, so the mark follows the
    # record that judged them rather than the one before.
    write_note({**RECORD, "readme_error": "HTTP 404: Not Found"}, tmp_path, today_iso="2026-06-06")
    write_note(RECORD, tmp_path, today_iso="2027-01-01")

    assert parse_frontmatter(_note(tmp_path).read_text())["readme"] == "fetched"


def test_the_fields_block_cannot_claim_a_readme_the_record_did_not_fetch(tmp_path: Path) -> None:
    result = write_note(
        {**RECORD, "fields": {**RECORD["fields"], "readme": "fetched"}, "readme_error": "boom"},
        tmp_path,
        today_iso="2026-06-06",
    )

    assert parse_frontmatter(_note(tmp_path).read_text())["readme"] == "unavailable"
    assert result["dropped_fields"] == ["readme"]


@pytest.mark.parametrize(
    "description",
    [
        "true",  # would parse as bool without quoting
        "- 1: a, b",  # leading dash + colon + comma
        'He said "hi" to all',  # embedded double quotes
        r"path C:\Users\x and a \ slash",  # embedded backslashes
        '"',  # a lone quote
        "ends with backslash \\",
        "tag: #grounded, value",
        "tabbed\tdescription",  # interior tab — bare YAML would reject the line
    ],
)
def test_a_github_description_survives_the_round_trip(tmp_path: Path, description: str) -> None:
    # A GitHub `description` is arbitrary text on the note's most YAML-hostile
    # line, and it is written unattended — a shape that breaks the block would
    # take the whole note out of the Repos Base.
    write_note(
        {**RECORD, "fields": {**RECORD["fields"], "description": description}},
        tmp_path,
        today_iso="2026-06-06",
    )
    assert parse_frontmatter(_note(tmp_path).read_text())["description"] == description


def test_write_creates_a_valid_note_from_a_sparse_record(tmp_path: Path) -> None:
    # A record assembled by hand or by the skill rather than piped straight from
    # `gather` still has to land a note `kboat-validate` accepts. `status` is
    # what makes it one: a required non-empty enum whose `unknown` member is the
    # schema default that fills it.
    write_note({**RECORD, "fields": {}}, tmp_path, today_iso="2026-06-06")

    fm = parse_frontmatter(_note(tmp_path).read_text())
    assert fm["status"] == "unknown"
    assert check_note("repo", dict(fm), f"Repos/{SLUG}.md") == []


def test_write_update_preserves_body_reading_and_added_date(tmp_path: Path) -> None:
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    path = _note(tmp_path)
    # Simulate the human editing the body and checking `reading`.
    path.write_text(
        path.read_text().replace("reading: false", "reading: true") + "\nmy hand notes\n"
    )

    bumped = {**RECORD, "fields": {**RECORD["fields"], "stars": 999}, "summary": "新しい要約。"}
    result = write_note(bumped, tmp_path, today_iso="2027-01-01")
    assert result["status"] == "updated"
    note = path.read_text()
    fm = parse_frontmatter(note)
    assert fm["stars"] == "999"  # refreshed
    assert fm["reading"] is True  # preserved
    assert fm["added_date"] == "2026-06-06"  # original kept, not bumped to 2027
    assert fm["refreshed_date"] == "2027-01-01"
    assert "summary: 新しい要約。" in note
    assert "my hand notes" in note  # body preserved


def test_write_cannot_be_told_to_overwrite_what_the_record_does_not_own(tmp_path: Path) -> None:
    # `reading` and `gone` are the human's, the date stamps are the schema's, and
    # `title` is read off the top level. A `fields` block carrying any of them (a
    # hand-assembled record, or a gather record fed back in) must not reach
    # them — nor may a key no schema knows reach the frontmatter.
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    path = _note(tmp_path)
    path.write_text(path.read_text().replace("reading: false", "reading: true"))

    result = write_note(
        {
            **RECORD,
            "fields": {
                **RECORD["fields"],
                "reading": False,
                "gone": True,  # only a human ticks it
                "added_date": "1999-01-01",
                "refreshed_date": "1999-01-01",
                "title": "someone/else",
                "invented_by_the_classifier": "x",
            },
        },
        tmp_path,
        today_iso="2027-01-01",
    )

    note = path.read_text()
    fm = parse_frontmatter(note)
    assert fm["reading"] is True
    assert fm["gone"] is False
    assert fm["added_date"] == "2026-06-06"
    assert fm["refreshed_date"] == "2027-01-01"
    assert fm["title"] == "google/A2A"  # the top-level value, not the one under `fields`
    assert "invented_by_the_classifier" not in note
    # Dropped, but not in silence — the caller is told what it sent that did not land.
    assert result["dropped_fields"] == [
        "reading",
        "gone",
        "added_date",
        "refreshed_date",
        "title",
        "invented_by_the_classifier",
    ]


def test_cataloguing_a_repository_ticked_gone_again_clears_the_tick_and_says_so(
    tmp_path: Path,
) -> None:
    # A record reaches the writer only after `gather` found GitHub showing the
    # repository, so the tick is false by then; left in place, every refresh would
    # skip the repository the human just catalogued again, and nothing would say so.
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    path = _note(tmp_path)
    path.write_text(path.read_text().replace("gone: false", "gone: true"))

    result = write_note(RECORD, tmp_path, today_iso="2027-01-01")

    assert result["status"] == "updated"
    assert result["gone_cleared"] is True
    assert parse_frontmatter(path.read_text())["gone"] is False


def test_a_write_to_a_note_not_ticked_gone_reports_no_clearing(tmp_path: Path) -> None:
    write_note(RECORD, tmp_path, today_iso="2026-06-06")

    assert "gone_cleared" not in write_note(RECORD, tmp_path, today_iso="2027-01-01")


def test_a_refused_write_clears_nothing(tmp_path: Path) -> None:
    # A note at the slug that is not this repository is refused, so neither its
    # tick nor the report may claim a clearing that never landed.
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    path = _note(tmp_path)
    path.write_text(
        path.read_text()
        .replace("gone: false", "gone: true")
        .replace(f"url: {URL}", "url: https://github.com/someone/else")
    )

    result = write_note(RECORD, tmp_path, today_iso="2027-01-01")

    assert result["status"] == "collision"
    assert "gone_cleared" not in result
    assert parse_frontmatter(path.read_text())["gone"] is True


def test_write_reports_nothing_dropped_when_nothing_was(tmp_path: Path) -> None:
    assert "dropped_fields" not in write_note(RECORD, tmp_path, today_iso="2026-06-06")


def test_every_field_gather_produces_is_one_the_note_takes(tmp_path: Path) -> None:
    # `dropped_fields` is a signal only if the happy path never trips it — the
    # skill relays it on every unattended run. Driven by the real producer, so a
    # key added to `github_fields` that `REPO` does not declare fails here
    # rather than turning every run into noise.
    result = write_note(
        {**RECORD, "fields": github_fields({}, today=date(2026, 6, 6))},
        tmp_path,
        today_iso="2026-06-06",
    )
    assert "dropped_fields" not in result


def test_write_update_preserves_a_github_field_the_record_omits(tmp_path: Path) -> None:
    # A partial `fields` block updates what it carries and preserves what it
    # does not — the shared merge rule, reached through the record translation.
    # The record makes the round trip through an LLM subagent between `gather`
    # and here, and a dropped empty-valued key is what that hop loses.
    write_note(RECORD, tmp_path, today_iso="2026-06-06")

    write_note({**RECORD, "fields": {"stars": 999}}, tmp_path, today_iso="2027-01-01")

    fm = parse_frontmatter(_note(tmp_path).read_text())
    assert fm["stars"] == "999"
    assert fm["homepage"] == "https://a2a-protocol.org/"
    assert fm["license"] == "apache-2.0"
    assert fm["topics"] == "[a2a, agents]"  # an inline list re-reads as its raw string


def test_write_update_keeps_prose_above_the_notes_section(tmp_path: Path) -> None:
    # `## Notes` is the writer's section; anything above it is the human's and
    # is not pulled down into it.
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    path = _note(tmp_path)
    path.write_text(path.read_text().replace("## Notes", "why I saved this\n\n## Notes\n\nverdict"))

    write_note(RECORD, tmp_path, today_iso="2027-01-01")

    assert path.read_text().rstrip().endswith("why I saved this\n\n## Notes\n\nverdict")


def test_write_collision_refuses_overwrite(tmp_path: Path) -> None:
    # Same slug, different repo url → 48-bit collision; must not overwrite. No
    # pair of URLs hashes alike, so the clash is staged: this repo's note is
    # moved onto the slug the other one names.
    clone = "https://github.com/evil/clone"
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    _note(tmp_path).rename(tmp_path / "Repos" / f"{note_slug(clone)}.md")
    other = {
        **RECORD,
        "slug": note_slug(clone),
        "url": clone,
        "title": "evil/clone",
        "fields": {**RECORD["fields"], "descrption": "typo"},
    }
    result = write_note(other, tmp_path, today_iso="2026-06-06")
    assert result["status"] == "collision"
    assert result["reason"] == "identity_differs"
    # No per-key report on a write that did not happen: every field is unset,
    # and the collision is the thing to act on.
    assert "dropped_fields" not in result
    # Original note untouched.
    note = tmp_path / "Repos" / f"{note_slug('https://github.com/evil/clone')}.md"
    assert parse_frontmatter(note.read_text())["title"] == "google/A2A"


def test_write_refuses_a_url_it_cannot_compare(tmp_path: Path) -> None:
    # A `url` hand-edited into a list (two clicks in Obsidian) decodes fine and
    # still cannot be matched against a string, so nothing shows the note to be
    # this repo — not even the record naming the very URL it was written from.
    # The check exists to refuse, so it fails closed rather than writing over
    # whatever is there.
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    path = _note(tmp_path)
    path.write_text(path.read_text().replace(f"url: {RECORD['url']}", f"url:\n  - {RECORD['url']}"))
    before = path.read_text()

    result = write_note(RECORD, tmp_path, today_iso="2027-01-01")

    assert result["status"] == "collision"
    assert result["reason"] == "unreadable_identity"
    assert path.read_text() == before
