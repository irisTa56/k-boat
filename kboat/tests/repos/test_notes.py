"""Tests for repo-note rendering and in-place frontmatter rewriting."""

from __future__ import annotations

import pytest

from kboat.repos.notes import (
    FrontmatterError,
    body_after_frontmatter,
    build_repo_note,
    parse_frontmatter,
    set_fields,
    yaml_scalar,
)


@pytest.mark.parametrize(
    "value",
    [
        "true",
        "False",
        "null",
        "NULL",
        "yes",
        "no",
        "on",
        "off",
        "none",
        "~",
        "123",
        "1.5",
        "-3",
        "1e9",
        "00",  # numeric forms
        "- leading dash",
        "? leading",
        "@ at",
        "* star",  # leading indicators
        "has: colon",  # a `: ` mapping separator
        "ends with colon:",  # a trailing colon
        ":leading colon",  # a leading colon
        "a # comment",  # a ` #` comment
        " leading space",
        "trailing space ",
    ],
)
def test_yaml_scalar_quotes_ambiguous_values(value: str) -> None:
    rendered = yaml_scalar(value)
    assert rendered.startswith('"') and rendered.endswith('"')


@pytest.mark.parametrize(
    "value",
    [
        "plain text",
        "Agent2Agent protocol",
        "apache-2.0",
        "google/A2A",
        "https://example.com/a?x=1:2",  # a colon inside a URL is safe bare
        "ratio 3:2 here",  # a colon with no following space is safe
        "free text, with a comma",  # a comma is safe in a plain (block) scalar
        "a (parenthetical) note",
    ],
)
def test_yaml_scalar_leaves_safe_values_bare(value: str) -> None:
    assert yaml_scalar(value) == value


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
def test_description_roundtrips_through_parser(description: str) -> None:
    note = build_repo_note({**FIELDS, "description": description})
    assert parse_frontmatter(note)["description"] == description


FIELDS = {
    "type": "repo",
    "title": "google/A2A",
    "url": "https://github.com/google/A2A",
    "homepage": "https://a2a-protocol.org/",
    "reading": False,
    "description": "An open protocol: agents talk.",  # contains a colon -> must quote
    "language": ["Shell"],
    "topics": ["a2a", "agents"],
    "stars": 23707,
    "archived": False,
    "created_at": "2025-03-25",
    "last_commit": "2026-05-01",
    "license": "apache-2.0",
    "role": "framework",
    "domain": ["ai-agents", "distributed-systems"],
    "summary": "エージェント間通信のオープンプロトコル。",
    "status": "recent",
    "added_date": "2025-05-12",
    "refreshed_date": "2026-06-06",
}


def test_build_repo_note_roundtrips_scalars() -> None:
    note = build_repo_note(FIELDS, notes_body="my hand-written thoughts")
    assert note.startswith("---\n")
    assert "## Notes\n\nmy hand-written thoughts\n" in note
    fm = parse_frontmatter(note)
    assert fm["type"] == "repo"
    assert fm["title"] == "google/A2A"
    assert fm["stars"] == "23707"  # parser returns scalars as strings
    assert fm["archived"] is False
    assert fm["reading"] is False
    # A value with a colon is quoted so YAML stays valid.
    assert 'description: "An open protocol: agents talk."' in note
    # Lists render inline (flow style), one line each.
    assert "topics: [a2a, agents]" in note
    assert "domain: [ai-agents, distributed-systems]" in note


def test_build_repo_note_field_order() -> None:
    note = build_repo_note(FIELDS)
    idx = {k: note.index(f"\n{k}:") for k in ("type", "url", "role", "status", "refreshed_date")}
    assert idx["type"] < idx["url"] < idx["role"] < idx["status"] < idx["refreshed_date"]


def test_empty_body_emits_bare_notes_section() -> None:
    note = build_repo_note(FIELDS)
    assert note.rstrip().endswith("## Notes")


def test_set_fields_preserves_other_fields_and_body() -> None:
    note = build_repo_note(FIELDS, notes_body="keep me")
    updated = set_fields(note, {"stars": 99999, "status": "dormant", "topics": ["x", "y"]})
    fm = parse_frontmatter(updated)
    assert fm["stars"] == "99999"
    assert fm["status"] == "dormant"
    assert "topics: [x, y]" in updated
    # Untouched judgement + body survive.
    assert fm["role"] == "framework"
    assert "summary: " in updated
    assert body_after_frontmatter(updated).strip().endswith("keep me")


def test_set_fields_missing_key_raises() -> None:
    note = build_repo_note(FIELDS)
    with pytest.raises(FrontmatterError):
        set_fields(note, {"nonexistent_field": "x"})


def test_parse_requires_frontmatter() -> None:
    with pytest.raises(FrontmatterError):
        parse_frontmatter("no frontmatter here")
