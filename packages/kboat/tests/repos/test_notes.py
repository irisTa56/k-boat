"""Tests for repo-note rendering and the in-place frontmatter rewrite `refresh` uses."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from kboat.frontmatter import FrontmatterError, body_after_frontmatter, parse_frontmatter
from kboat.repos.notes import set_fields
from kboat.schema import REPO
from kboat.write import build_note

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


def _repo_note(fields: Mapping[str, object], body: str = "") -> str:
    """A repo note, rendered without touching a vault."""
    return build_note(REPO, fields, body)


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
    note = _repo_note({**FIELDS, "description": description})
    assert parse_frontmatter(note)["description"] == description


def test_repo_scalars_roundtrip_through_the_parser() -> None:
    note = _repo_note(FIELDS)
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


def test_set_fields_preserves_other_fields_and_body() -> None:
    note = _repo_note(FIELDS, body="## Notes\n\nkeep me")
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
    note = _repo_note(FIELDS)
    with pytest.raises(FrontmatterError):
        set_fields(note, {"nonexistent_field": "x"})
