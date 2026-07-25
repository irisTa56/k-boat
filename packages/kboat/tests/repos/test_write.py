"""Tests for `write_note` — note assembly, body/reading/added_date preservation, de-dup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kboat.repos.notes import parse_frontmatter
from kboat.repos.write import write_note

RECORD: dict[str, Any] = {
    "slug": "abc123def456",
    "url": "https://github.com/google/A2A",
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
}


def test_write_creates_note(tmp_path: Path) -> None:
    result = write_note(RECORD, tmp_path, today_iso="2026-06-06")
    assert result["status"] == "created"
    note = (tmp_path / "Repos" / "abc123def456.md").read_text()
    fm = parse_frontmatter(note)
    assert fm["type"] == "repo"
    assert fm["role"] == "framework"
    assert fm["status"] == "recent"
    assert fm["added_date"] == "2026-06-06"
    assert 'description: "An open protocol: agents talk."' in note  # quoted, valid YAML
    assert "topics: [a2a, agents]" in note
    assert note.rstrip().endswith("## Notes")  # empty body


def test_write_update_preserves_body_reading_and_added_date(tmp_path: Path) -> None:
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    path = tmp_path / "Repos" / "abc123def456.md"
    # Simulate the human editing the body and checking `reading`.
    edited = (
        path.read_text().replace("reading: false", "reading: true").rstrip() + "\nmy hand notes\n"
    )
    path.write_text(edited)

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


def test_write_update_keeps_an_empty_notes_section_empty(tmp_path: Path) -> None:
    # A fresh note ends at a bare `## Notes`. That is "nowhere to put notes yet",
    # not "the body is what precedes the heading" — reading it as the latter
    # would pull frontmatter-adjacent prose down into the section.
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    path = tmp_path / "Repos" / "abc123def456.md"

    write_note(RECORD, tmp_path, today_iso="2027-01-01")

    assert path.read_text().rstrip().endswith("## Notes")


def test_write_update_does_not_split_a_body_at_a_quoted_heading(tmp_path: Path) -> None:
    # The quoted headings come *before* the real section, so a splitter that
    # matched one of them would cut the body in the wrong place. A four-backtick
    # block quoting a three-backtick one is the ordinary way to document
    # markdown, and reads as two fences to anything that ignores marker length.
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    path = tmp_path / "Repos" / "abc123def456.md"
    body = (
        "### Notes on the API\n\nSee the ## Notes section.\n\n"
        "````markdown\n```\n## Notes\n```\n````\n\n"
        "## Notes\n\nwhat I actually think"
    )
    path.write_text(path.read_text().replace("## Notes", body))

    write_note(RECORD, tmp_path, today_iso="2027-01-01")

    assert path.read_text().rstrip().endswith("## Notes\n\nwhat I actually think")


def test_write_collision_refuses_overwrite(tmp_path: Path) -> None:
    write_note(RECORD, tmp_path, today_iso="2026-06-06")
    # Same slug, different repo url → 48-bit collision; must not overwrite.
    other = {**RECORD, "url": "https://github.com/evil/clone", "title": "evil/clone"}
    result = write_note(other, tmp_path, today_iso="2026-06-06")
    assert result["status"] == "collision"
    # Original note untouched.
    assert (
        parse_frontmatter((tmp_path / "Repos" / "abc123def456.md").read_text())["title"]
        == "google/A2A"
    )
