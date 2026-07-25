"""Tests for schema-driven note assembly (`build_note` / `render_field`)."""

from __future__ import annotations

import pytest
import yaml

from kboat.frontmatter import parse_flow_list, parse_frontmatter, yaml_list
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
        ("[a: b, c]", "a mapping entry, which needs no braces inside a sequence"),
        ("[a:]", "likewise a mapping key with nothing after it"),
    ],
)
def test_a_string_that_is_no_flow_sequence_stays_a_valid_scalar(value: str, why: str) -> None:
    # A value that carries its own syntax into the block costs the whole note —
    # the frontmatter stops parsing and Obsidian drops it from every Base. Read
    # as a scalar, a wrong-typed value costs only its own field.
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
        (
            "['it''s, ok', ai]",
            ["it's, ok", "ai"],
            "a single-quoted item escapes its quote by doubling it, so `''` is a character",
        ),
        (
            "[Moore's law, ai]",
            ["Moore's law", "ai"],
            "an apostrophe mid-item is a character, not the start of a quoted scalar",
        ),
        ("[a, b,]", ["a", "b"], "a trailing comma closes the last item, it does not open one"),
        (
            "[https://x.com/y, z]",
            ["https://x.com/y", "z"],
            "a colon with nothing after it is part of the scalar, not a mapping",
        ),
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


@pytest.mark.parametrize(
    "items",
    [
        ['say "hi"', "plain"],
        ["back\\slash", "ends with a backslash\\"],
        ["a, b", "c"],
        ["col\tumn", "line\none"],
        ["[bracketed]", "{braced}"],
        ["true", "42", "- dash", "# hash"],
    ],
)
def test_parse_flow_list_inverts_yaml_list(items: list[str]) -> None:
    # `parse_flow_list` is only safe to re-render through `yaml_list` because it
    # is that function's inverse — including over the escaping `_quote` applies.
    assert parse_flow_list(yaml_list(items)) == items


def test_block_list_is_multiline() -> None:
    note = build_note(SOURCE, {"type": "source", "title": "T", "topics": ["a", "b"]})
    assert "topics:\n  - a\n  - b" in note
    assert parse_frontmatter(note)["topics"] == ["a", "b"]


@pytest.mark.parametrize(
    ("value", "expected", "why"),
    [
        ("[a, b]", ["a", "b"], "an inline list re-read as its source becomes the block form"),
        ("ai", "ai", "a plain string has no items, so it stays the value it is"),
        (5, "5", "likewise anything else that is not a list"),
        (None, [], "None is the absent value the empty list stands for"),
    ],
)
def test_a_block_list_field_never_erases_what_it_was_given(
    value: object, expected: object, why: str
) -> None:
    # `[]` for a value that is not a list would report a write that threw the
    # value away. A wrong-typed one is left for `kboat-validate` to report.
    note = build_note(SOURCE, {"type": "source", "title": "T", "topics": value})

    assert parse_frontmatter(note)["topics"] == expected, why


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
