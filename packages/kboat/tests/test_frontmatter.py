"""Tests for the shared frontmatter primitives' public helpers."""

from __future__ import annotations

import pytest
import yaml

from kboat.frontmatter import (
    FrontmatterError,
    body_after_frontmatter,
    is_iso_date,
    parse_entries,
    parse_frontmatter,
    set_field,
    strip_frontmatter,
    yaml_scalar,
)
from kboat.schema import SOURCE
from kboat.write import build_note, carried_entries


def _fm(*lines: str) -> str:
    return "---\n" + "\n".join(lines) + "\n---\n"


def _as_yaml(note: str) -> object:
    """The frontmatter block as a real YAML parser reads it, or None if it cannot.

    None means "no verdict": several shapes below are deliberately malformed, and
    the scanner's job there is to carry them, not to repair them.
    """
    try:
        return yaml.safe_load(note.split("---\n")[1])
    except yaml.YAMLError:
        return None


def _unmodelled(text: str) -> list[list[str]]:
    """Each unmodelled entry's verbatim lines, in source order."""
    return [list(e.lines) for e in parse_entries(text) if not e.modelled]


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
    "value",
    [
        'say "hi"',  # an embedded double quote
        "back\\slash",  # a backslash
        'Official Repository for "Eureka: a title" (ICLR 2024)',  # quotes + a `: `
        "line one\nline two",  # an embedded newline
        "col\tumn",  # an embedded tab
        'mix "q" and\nnewline\tand tab',
        "literal backslash-n: a\\nb",  # a `\` + `n`, NOT a newline — stays literal
        "ends with a backslash\\",  # a trailing backslash
        # Characters that end a line for one reader of this vault and not
        # another: `str.splitlines` breaks on all of them, YAML and Obsidian on
        # none. Raw in a note they would make the value two lines here and one
        # everywhere else — so they are escaped, and the tail of a summary
        # cannot arrive as a property of the note.
        "line\u2028separator",
        "paragraph\u2029separator",
        "next\x85line",
        "vertical\x0btab",
        "form\x0cfeed",
        "record\x1eseparator",
        "escape\x1bchar",
        "null\x00byte",
    ],
)
def test_quoted_scalar_round_trips(value: str) -> None:
    # yaml_scalar (-> _quote) and parse_frontmatter (-> _unquote) are inverses,
    # even for embedded quotes / backslashes / control chars, on one valid line.
    note = f"---\nx: {yaml_scalar(value)}\n---\n"
    assert "\n" not in note.split("---")[1].strip()  # the value stays on one line
    assert parse_frontmatter(note)["x"] == value
    assert yaml.safe_load(note.split("---\n")[1])["x"] == value  # and to another reader


@pytest.mark.parametrize(
    ("written", "expected", "why"),
    [
        ('"a\\u2028b"', "a\u2028b", "the escape Obsidian writes for a line separator"),
        ('"a\\x1bb"', "a\x1bb", "and the two-digit form of the same idea"),
        ('"a\\Nb"', "a\x85b", "a named escape this writer never emits but YAML defines"),
        ('"a\\x1"', "a\\x1", "an escape short of its width names nothing"),
        ('"a\\ud800b"', "a\\ud800b", "nor does a surrogate, which no UTF-8 file can hold"),
        ('"a\\qb"', "a\\qb", "nor an escape YAML does not define"),
        ('"a\\U0001f600b"', "a\U0001f600b", "the eight-digit form, which YAML also defines"),
        ('"a\\U00110000b"', "a\\U00110000b", "though not one naming no code point at all"),
    ],
)
def test_an_escape_this_writer_never_emits_is_still_read_as_what_it_means(
    written: str, expected: str, why: str
) -> None:
    # A note is written by Obsidian too, and read back here. An escape decoded as
    # its own literal text would be a value that changes each time it passes
    # through — and one invented from an escape that names nothing would be worse.
    assert parse_frontmatter(f"---\nx: {written}\n---\n")["x"] == expected, why


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("Rust\xa0#1 の話", "a non-breaking space, which is what scraped HTML leaves"),
        ("要約\u3000#補足 と続き", "an ideographic space, ordinary in Japanese page text"),
    ],
)
def test_a_hash_after_a_space_yaml_does_not_know_is_not_a_comment(value: str, why: str) -> None:
    # A comment opens after a space or a tab. Reading a wider set as whitespace
    # would cut the value off at a character YAML keeps — and `title` and
    # `summary` are written from page text, where those characters live.
    note = f"---\nx: {yaml_scalar(value)}\n---\n"

    assert parse_frontmatter(note)["x"] == value, why
    assert yaml.safe_load(note.split("---\n")[1])["x"] == value, why
    # A comment after a space kept being one, which is the reason for the rule.
    assert parse_frontmatter("---\nx: kept # dropped\n---\n")["x"] == "kept"


def test_parse_requires_frontmatter() -> None:
    with pytest.raises(FrontmatterError):
        parse_frontmatter("no frontmatter here")


def test_strip_frontmatter_returns_body_when_block_present() -> None:
    text = "---\ntags: [daily]\n---\n\n## Notes\n- a\n"
    assert strip_frontmatter(text) == "\n## Notes\n- a\n"


def test_strip_frontmatter_keeps_thematic_break_in_body() -> None:
    # Only the first `---` after the opener closes the block; a later `---`
    # thematic break stays in the returned body.
    text = "---\ntags: x\n---\nintro\n\n---\n\nmore\n"
    assert strip_frontmatter(text) == "intro\n\n---\n\nmore\n"


@pytest.mark.parametrize("ending", ["\r\n", "\r"], ids=["crlf", "cr"])
def test_a_note_written_with_another_line_ending_reads_and_rewrites_as_itself(
    ending: str,
) -> None:
    # Each of YAML's three terminators, because a rewriter puts back what it took
    # off: read one as no terminator at all and the rewritten line runs into the
    # one below it, joining two properties into nonsense.
    text = ending.join(["---", "tags: x", "n: 1", "---", "body", ""])

    assert strip_frontmatter(text) == f"body{ending}"
    assert parse_frontmatter(text)["tags"] == "x"
    assert set_field(text, "n", "2") == text.replace("n: 1", "n: 2")


def test_strip_frontmatter_tolerates_whitespace_around_fence() -> None:
    # Deliberate widening over the old dailynotes scanner: fences are matched via
    # the module's shared `_fence_bounds` (`.strip() == "---"`), so surrounding
    # whitespace on a fence line is tolerated rather than treated as "not a fence".
    assert strip_frontmatter("  ---\ntags: x\n  ---\nbody\n") == "body\n"
    assert strip_frontmatter("--- \ntags: x\n--- \nbody\n") == "body\n"


def test_strip_frontmatter_passthrough_without_opening_fence() -> None:
    # No leading `---`: not frontmatter, returned whole (the lenient contract).
    text = "just a plain line\n"
    assert strip_frontmatter(text) == text


def test_strip_frontmatter_passthrough_when_fence_unclosed() -> None:
    # A leading `---` with no closing fence is not frontmatter; keep it whole,
    # where body_after_frontmatter would raise.
    text = "---\nstray dashes, then prose\n"
    assert strip_frontmatter(text) == text
    with pytest.raises(FrontmatterError):
        body_after_frontmatter(text)


def test_strip_frontmatter_passthrough_on_empty_text() -> None:
    assert strip_frontmatter("") == ""


def test_everything_representable_is_modelled() -> None:
    text = _fm("title: T", "bare:", "empty: []", "flag: true", "topics:", "  - a", "  - b")

    assert parse_frontmatter(text) == {
        "title": "T",
        "bare": None,
        "empty": [],
        "flag": True,
        "topics": ["a", "b"],
    }
    assert _unmodelled(text) == []


def test_unindented_list_items_still_parse_as_a_list() -> None:
    # YAML allows a block list at the key's own indent; that is representable, so
    # it must not be diverted into an unmodelled entry.
    text = _fm("topics:", "- a", "- b")
    assert parse_frontmatter(text) == {"topics": ["a", "b"]} and _unmodelled(text) == []


@pytest.mark.parametrize(
    ("title", "why"),
    [
        ('"Advent of Code 2026 [Day #3]"', "a bracket and a `#` inside quotes"),
        ('"a ] and a }"', "closers inside quotes"),
        ("How to type [ on a Mac", "an unclosed bracket in a plain scalar"),
        ('"[unclosed"', "an unclosed bracket inside quotes"),
        ("T # a remark", "a trailing comment"),
    ],
)
def test_a_scalar_that_merely_looks_like_a_collection_stays_one_line(title: str, why: str) -> None:
    """A value read as an open `[` swallows the lines after it, and every field
    they held disappears from the note — silently, since what remains still
    parses. Bounding this is why the scan is quote-aware and stops at the next
    column-zero key."""
    fields = parse_frontmatter(_fm(f"title: {title}", "url: https://x", "added_date: 2026-07-19"))

    assert fields["url"] == "https://x", why
    assert fields["added_date"] == "2026-07-19", why


def test_a_comment_stands_on_its_own_rather_than_clouding_a_key() -> None:
    """Inside the key's entry a comment would make the key unreadable — a note
    whose `url` is trailed by one could never be written again — and it would
    vanish the moment that key was rewritten."""
    text = _fm("url: https://x", "# kept for the mapping thread", "topics:", "  - a")
    entries = parse_entries(text)

    assert parse_frontmatter(text) == {"url": "https://x", "topics": ["a"]}
    assert [e.lines for e in entries if e.key is None] == [("# kept for the mapping thread",)]


def test_a_comment_between_a_key_and_its_block_does_not_break_the_block() -> None:
    text = _fm("topics:", "# why these", "  - mapping", "  - walking")

    assert parse_frontmatter(text) == {"topics": ["mapping", "walking"]}


def test_an_indented_hash_is_block_content_not_a_comment() -> None:
    # Inside a block scalar a `#` line is text the human wrote, not an annotation.
    entries = parse_entries(_fm("notes: |", "  # a heading in my notes", "  more"))

    assert [e.lines for e in entries] == [("notes: |", "  # a heading in my notes", "  more")]


def test_every_entry_carries_its_own_source_lines() -> None:
    # The verbatim source is what lets a writer put an entry back untouched, so
    # a modelled entry has to carry it too — not only the unmodellable ones.
    entries = parse_entries(_fm("title: T", "topics:", "  - a"))

    assert [e.lines for e in entries] == [("title: T",), ("topics:", "  - a")]
    assert all(e.modelled for e in entries)


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("kebab-case-key: v", "hyphen is outside the reader's key grammar"),
        ('"quoted key": v', "a quoted key likewise"),
        ("not a key at all", "no colon"),
        ("- orphan item", "a list item under no key"),
    ],
)
def test_an_unmodellable_top_level_line_stands_alone(line: str, why: str) -> None:
    """It must not be absorbed into the neighbouring key, whose value would then
    change from under it."""
    text = _fm("title: T", line)

    assert parse_frontmatter(text) == {"title": "T"}, why
    assert _unmodelled(text) == [[line]], why


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        (("meta:", "  nested: x", "  other: y"), "a nested mapping, not a list"),
        (("desc: |", "  first line", "  second line"), "a block scalar"),
        (("topics:", "  - a", "  stray: x"), "a list with a non-item line in the block"),
    ],
)
def test_a_block_the_reader_cannot_model_is_one_entry(lines: tuple[str, ...], why: str) -> None:
    """The key line stays with its block and the key is absent from the fields,
    so a writer emits it once, from the source lines."""
    text = _fm("title: T", *lines)

    assert parse_frontmatter(text) == {"title": "T"}, why
    assert _unmodelled(text) == [list(lines)], why


@pytest.mark.parametrize(
    "blank",
    ["", "   ", "\t", "  \t "],
    ids=["empty", "spaces", "tab", "indented-mixed"],
)
def test_a_blank_line_changes_nothing_wherever_it_lands(blank: str) -> None:
    """An editor leaves a whitespace-only line behind routinely. An *indented*
    one is the dangerous shape: read as a continuation it would make the entry
    above it unmodellable, so the field would vanish from every reader — the
    lifecycle dates, the collision check's identity, everything."""
    fields = {"title": "T", "filed_date": None, "topics": ["a", "b"], "url": "https://x"}
    body = ("title: T", "filed_date:", "topics:", "  - a", "  - b", "url: https://x")

    for at in range(len(body) + 1):
        text = _fm(*body[:at], blank, *body[at:])
        assert parse_frontmatter(text) == fields, f"blank at index {at}"
        assert _unmodelled(text) == [], f"blank at index {at}"


# Frontmatter shapes a hand edit or a foreign tool can leave behind, each
# checked by every whole-note property below.
SHAPES = [
    ("title: T", "topics:", "  - a", "  - b"),
    ("title: T", "kebab-key: v", "- orphan item"),
    ("meta:", "  nested: x", "title: T", "not a key at all"),
    ("desc: |", "  first line", "title: T", "tags: [a, b]"),
    ("title: T", "- orphan item", "bare:"),
    ("bare:", "- orphan item", "title: T"),
    ("  stray: x", "title: T"),
    # A key outside the reader's grammar that owns a block. Undecodable, but
    # its block is still its own: severed and hoisted, the two halves land on
    # opposite sides of the note and the frontmatter stops parsing at all.
    ("my-aliases:", "- Working title", "- Second", "title: T"),
    ("title: T", "my-key:", "  a: 1", "  b: 2"),
    ('"quoted key":', "  nested: x", "title: T"),
    # A value that runs past its own line: the closing bracket sits at column
    # zero, and a block scalar's paragraph break is a blank line that means
    # something.
    ("topics: [", "  mapping,", "  walking", "]", "title: T"),
    ("notes: |", "  first paragraph", "", "  second paragraph", "title: T"),
    # Comments have no value of their own, so they read as a remark on
    # whichever line they follow.
    ("topics:", "# why these", "  - mapping", "title: T"),
    ("my-aliases: # working titles", "- Draft one", "- Draft two", "title: T"),
    # A bracket or a `#` inside a quoted scalar is text, not structure.
    # Counting the characters instead lets an ordinary title read as an
    # unclosed collection and swallow every line after it.
    ('title: "Advent of Code 2026 [Day #3]"', "url: https://x", "added_date: 2026-07-19"),
    ('title: "trailing quote \\" inside"', "url: https://x"),
    ("topics: [Moore's law, ai]", "some-key: preserve me", "title: T"),
    ("title: T", "", "# a comment after a blank", "url: https://x"),
    # A comment owns nothing: gathering the item below it would hand that item to
    # whichever key the comment later lands under.
    ("aliases:", "  - one", "url: https://x", "# note", "- orphan", "k: v"),
]


@pytest.mark.parametrize("lines", SHAPES)
def test_the_entries_account_for_every_line_exactly_once(lines: tuple[str, ...]) -> None:
    """Conservation, checkable without an oracle and independent of the writer.
    Comparing a rewritten note against `parse_entries` cannot catch a line the
    scanner *invents*, because the invention lands on both sides. Counted rather
    than sequenced, since a comment lifted out of a block legitimately moves; what
    it must never do is arrive twice or not at all."""
    text = _fm(*lines)

    got = [line for e in parse_entries(text) for line in e.lines]

    assert sorted(g for g in got if g.strip()) == sorted(x for x in lines if x.strip())


@pytest.mark.parametrize(
    ("lines", "written"),
    [
        (("topics: [Moore's law, ai]", "some-key: preserve me", "title: T"), {"topics": ["x"]}),
        (("title: T", "# why I kept this", "url: https://x"), {"title": "new"}),
        (("my-aliases:", "- Draft one", "title: T"), {"title": "new"}),
        (("notes: |", "  first", "", "  second", "title: T"), {"title": "new"}),
    ],
)
def test_writing_a_field_takes_nothing_else_with_it(
    lines: tuple[str, ...], written: dict[str, object]
) -> None:
    """The deletion path. `carried` drops an entry once the record writes its key,
    so a line wrongly absorbed into that entry is lost — which only happens when
    something *is* written, the case an empty-`fields` property test never
    reaches."""
    text = _fm(*lines)

    got = build_note(SOURCE, written, carried=carried_entries(parse_entries(text), written))

    for line in lines:
        if line.strip() and not line.startswith(tuple(f"{k}:" for k in written)):
            assert line in got.splitlines(), line


@pytest.mark.parametrize("lines", SHAPES)
def test_a_writers_reordering_cannot_change_what_the_entries_mean(lines: tuple[str, ...]) -> None:
    """Classification depends on what precedes a line, so putting entries back in
    another order could change their meaning. `build_note`'s order is the one
    that has to be safe: it hoists schema fields to canonical positions and
    appends the rest, so replaying through *that* is the property worth pinning —
    replaying in source order would only re-check the input. Order itself is
    allowed to change; what each entry says is not.

    The YAML oracle is the load-bearing half. This module is a scanner, not a
    parser, so a note it rewrote into something no longer parseable still
    re-scans to the same entries — it would be grading its own homework. Where
    the input is itself malformed there is nothing to hold the output to, and
    only the entry property applies.
    """
    text = _fm(*lines)
    entries = parse_entries(text)
    written = build_note(SOURCE, {}, carried=entries)

    assert sorted(parse_entries(written), key=str) == sorted(entries, key=str)
    if (expected := _as_yaml(text)) is not None:
        assert _as_yaml(written) == expected


class TestIsIsoDate:
    @pytest.mark.parametrize("value", ["2026-06-01", "2024-02-29", "0001-01-01"])
    def test_accepts_a_canonical_calendar_date(self, value: str) -> None:
        assert is_iso_date(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "20260601",  # basic form: parses, but does not order alongside the canonical one
            "2026-W23-1",  # week date: same
            "2026-6-1",  # unpadded
            "2026-13-45",  # well-shaped, but no such day
            "2026-02-29",  # nor this one — 2026 is not a leap year
            "2026-06-01T00:00:00Z",  # a timestamp is not a date
            "2026/06/01",
            # Arabic-Indic digits, written as escapes so they stay visible in a
            # diff. The pattern's `\\d` admits them; only the parse rejects them,
            # so this is what stops a value reaching the lexicographic compare.
            "\u0662\u0660\u0662\u0666-\u0660\u0666-\u0660\u0661",
            " 2026-06-01",  # a quoted hand edit reaches here padded; it is drift
            "2026-06-01 ",
            "",
            "   ",
        ],
    )
    def test_rejects_everything_else(self, value: str) -> None:
        assert is_iso_date(value) is False

    @pytest.mark.parametrize("value", [None, [], 20260601, True])
    def test_rejects_a_non_string(self, value: object) -> None:
        assert is_iso_date(value) is False
