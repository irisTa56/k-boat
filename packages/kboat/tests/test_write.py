"""Tests for schema-driven note assembly (`build_note` / `render_field`)."""

from __future__ import annotations

import pytest
import yaml

from kboat.frontmatter import parse_frontmatter
from kboat.schema import REPO, SOURCE, Field, Kind
from kboat.write import build_note, render_field


def test_render_field_by_kind() -> None:
    assert render_field(Field("b", Kind.BOOL), True) == "true"
    assert render_field(Field("b", Kind.BOOL), False) == "false"
    assert render_field(Field("n", Kind.INT, default=0), 42) == "42"
    assert render_field(Field("n", Kind.INT, default=0), None) == "0"
    assert render_field(Field("l", Kind.STR_LIST), ["a", "b"]) == "[a, b]"
    assert render_field(Field("l", Kind.STR_LIST), []) == "[]"
    # A list re-read from a note as its raw inline text stays that text; reading
    # it as "not a list" and emitting `[]` would delete the items.
    assert render_field(Field("l", Kind.STR_LIST), "[a, b]") == "[a, b]"
    assert render_field(Field("l", Kind.STR_LIST), None) == "[]"
    assert render_field(Field("s", Kind.STR), "hi") == "hi"
    assert render_field(None, "x") == "x"  # unknown field → plain scalar


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("agents: tooling", "a plain string is no sequence at all"),
        ('[a, "b]', "an unclosed quote"),
        ("[a, [b]", "a nested collection commas cannot be split on"),
        ("[{a]", "likewise a mapping"),
    ],
)
def test_a_string_that_is_no_flow_sequence_is_quoted(value: str, why: str) -> None:
    # A value that carries its own syntax into the block costs the whole note —
    # the frontmatter stops parsing and Obsidian drops it from every Base.
    # Quoted, a wrong-typed value costs only its own field.
    note = build_note(REPO, {"type": "repo", "title": "r", "topics": value})

    assert parse_frontmatter(note)["topics"] == value, why
    assert yaml.safe_load(note.split("---\n")[1])["topics"] == value, why


@pytest.mark.parametrize(
    ("value", "expected", "why"),
    [
        ("[a, b]", ["a", "b"], "the plain shape the reader hands back"),
        ("  [a, b]  ", ["a", "b"], "edge whitespace would otherwise be spliced in bare"),
        ("[a,\nb]", ["a", "b"], "a second line, re-rendered onto one"),
        ('["a, b", plain]', ["a, b", "plain"], "a quoted item holding the separator"),
        ("[a\tb]", ["a\tb"], "a tab is a flow special, so the item comes back quoted"),
        ("[a #b]", ["a #b"], "likewise a comment marker"),
        ("[- a]", ["- a"], "likewise a leading indicator"),
        ("[]", [], "an empty sequence stays one"),
    ],
)
def test_a_flow_sequence_is_read_back_and_re_rendered(
    value: str, expected: list[str], why: str
) -> None:
    # Re-rendering rather than passing the source through is what puts every
    # item through `yaml_list`'s quoting, whatever the string it arrived in.
    note = build_note(REPO, {"type": "repo", "title": "r", "topics": value})

    assert yaml.safe_load(note.split("---\n")[1])["topics"] == expected, why


def test_block_list_is_multiline() -> None:
    note = build_note(SOURCE, {"type": "source", "title": "T", "topics": ["a", "b"]})
    assert "topics:\n  - a\n  - b" in note
    assert parse_frontmatter(note)["topics"] == ["a", "b"]


def test_inline_list_is_flow_style() -> None:
    note = build_note(REPO, {"type": "repo", "title": "r", "topics": ["x", "y"]})
    assert "topics: [x, y]" in note


def test_inline_list_item_with_comma_is_quoted() -> None:
    # A comma is safe in a plain scalar but breaks a flow list item, so it quotes.
    note = build_note(REPO, {"type": "repo", "topics": ["a, b", "plain"]})
    assert 'topics: ["a, b", plain]' in note


def test_empty_list_renders_as_flow_empty() -> None:
    # Empty block-style and inline-style lists both render `[]` (re-read as []).
    assert "topics: []" in build_note(SOURCE, {"type": "source", "topics": []})
    assert "topics: []" in build_note(REPO, {"type": "repo", "topics": []})


def test_url_is_not_quoted() -> None:
    note = build_note(SOURCE, {"type": "source", "url": "https://ex.com/a?x=1:2"})
    assert "url: https://ex.com/a?x=1:2" in note  # bare — a URL colon is safe


def test_frontmatter_only_has_no_body() -> None:
    note = build_note(SOURCE, {"type": "source", "title": "T"})
    assert note == "---\ntype: source\ntitle: T\n---\n"


def test_body_appended_after_blank_line() -> None:
    note = build_note(REPO, {"type": "repo"}, body="## Notes\n\nhand notes\n")
    assert note.endswith("---\n\n## Notes\n\nhand notes\n")


def test_unknown_keys_are_appended_not_dropped() -> None:
    note = build_note(SOURCE, {"type": "source", "extra": "kept"})
    assert "extra: kept" in note


def test_only_present_keys_are_written() -> None:
    note = build_note(SOURCE, {"type": "source", "title": "T"})
    keys = set(parse_frontmatter(note))
    assert keys == {"type", "title"}  # absent schema fields are not emitted
