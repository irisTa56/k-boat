"""Tests for the concept-note shape classifier.

Every case is stated as a note the writer could actually meet, because `shape` is
read for one purpose: whether a new group owes the note a heading over the claims
already in it.
"""

from __future__ import annotations

import pytest

from kboat.concept import FLAT, GROUPED, _heading_level, classify

FLAT_NOTE = """---
title: KV cache
---

A lead paragraph.

## Observations

- [definition] Something the first reading said #grounded
- [source] First reading — <https://example.com/a>

## Relations

- managed_by [[PagedAttention]]
"""

GROUPED_NOTE = """---
title: KV cache
---

A lead paragraph.

## Observations

### What the first reading contributed

- [definition] Something #grounded
- [source] First reading — <https://example.com/a>

### What the second reading contributed

- [insight] Something else #dialogue
- [source] Second reading — <https://example.com/b>

## Relations

- managed_by [[PagedAttention]]
"""


def _body(observations: str) -> str:
    return f"## Observations\n\n{observations}\n\n## Relations\n\n- related_to [[Other]]\n"


def test_claims_with_no_group_heading_are_flat() -> None:
    assert classify(FLAT_NOTE) == FLAT


def test_claims_under_group_headings_are_grouped() -> None:
    assert classify(GROUPED_NOTE) == GROUPED


def test_an_empty_observations_section_is_flat() -> None:
    # There is nothing to wrap, but that is the writer's one clause to handle,
    # not a shape of its own -- see `classify`.
    assert classify(_body("")) == FLAT


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n\n",
        "## Relations\n\n- related_to [[Other]]\n",
        "# Error\n\nNote not found: no note matched that identifier.\n",
    ],
)
def test_text_carrying_no_observations_heading_is_refused(text: str) -> None:
    # Not a third shape: the tool was not handed a concept note. Answering `flat`
    # for these would send the writer's flat branch at a note it never saw, and on
    # a really-grouped one that edit succeeds -- the anchor is there -- leaving a
    # heading over the note's real first group that nothing reports.
    assert classify(text) is None


def test_a_note_with_no_relations_section_reads_to_the_end() -> None:
    # The Observations body runs to the end of the document when nothing closes it.
    assert classify("## Observations\n\n### A group\n\n- [x] y\n") == GROUPED


def test_a_deeper_heading_does_not_open_a_group() -> None:
    # `####` is a group's own internal structure; reading it as a group would
    # make a note look grouped while its earliest insight is still bare.
    assert classify(_body("#### A sub-point\n\n- [definition] x #grounded")) == FLAT


@pytest.mark.parametrize(
    "heading", ["## A level-two heading", "####### Seven hashes", "###NoSpace"]
)
def test_only_a_level_three_atx_heading_opens_a_group(heading: str) -> None:
    assert classify(_body(f"{heading}\n\n- [definition] x #grounded")) == FLAT


def test_a_group_heading_after_relations_does_not_count() -> None:
    note = "## Observations\n\n- [definition] x #grounded\n\n## Relations\n\n### Not a group\n"
    assert classify(note) == FLAT


@pytest.mark.parametrize("fence", ["```", "```markdown", "~~~"])
def test_a_group_heading_inside_a_fence_does_not_count(fence: str) -> None:
    close = "~~~" if fence.startswith("~") else "```"
    assert classify(_body(f"{fence}\n### Sample heading\n{close}")) == FLAT


def test_a_group_heading_after_a_closed_fence_still_counts() -> None:
    assert classify(_body("```\n### Sample\n```\n\n### A real group\n\n- [x] y")) == GROUPED


def test_a_backtick_fence_with_a_backtick_in_its_info_string_opens_nothing() -> None:
    # CommonMark, and what the editor applies: such a line is text, so the
    # heading below it is a heading.
    assert classify(_body("``` `not a fence`\n### A real group\n\n- [x] y")) == GROUPED


def test_a_shorter_run_does_not_close_a_longer_fence() -> None:
    assert classify(_body("````\n```\n### Sample heading\n````")) == FLAT


def test_a_longer_run_closes_a_shorter_fence() -> None:
    assert classify(_body("```\n### Sample\n````\n\n### A real group\n\n- [x] y")) == GROUPED


def test_the_other_fence_character_does_not_close_a_fence() -> None:
    # A `~~~` line inside a backtick fence is content, so the heading under it
    # stays hidden and the note is still flat.
    assert classify(_body("```\n~~~\n### Sample heading\n```")) == FLAT


def test_a_closing_fence_may_not_carry_trailing_text() -> None:
    assert classify(_body("```\n``` still open\n### Sample heading\n```")) == FLAT


def test_a_closing_fence_may_carry_trailing_whitespace() -> None:
    # The editor closes on a marker with nothing but whitespace after it.
    assert classify(_body("```\n### Sample\n```   \n\n### A real group\n\n- [x] y")) == GROUPED


def test_a_tilde_fence_opens_even_with_a_backtick_in_its_info_string() -> None:
    # The info-string guard is the backtick fence's alone; a `~~~` fence opens
    # whatever its info string carries, so the heading inside it stays hidden.
    assert classify(_body("~~~ x`y\n### Sample heading\n~~~")) == FLAT


def test_an_anchor_inside_a_fence_is_not_an_anchor() -> None:
    # The only `## Observations` is fenced, so it is not one -- the text carries
    # no section to report a shape for.
    note = "```\n## Observations\n### Sample\n```\n\n## Relations\n\n- related_to [[Other]]\n"
    assert classify(note) is None


def test_an_anchor_is_matched_on_the_stripped_line() -> None:
    # The editor matches `line.strip()`, so trailing whitespace still anchors --
    # and a note whose anchors carry it must not read as missing them.
    note = "## Observations  \n\n### A group\n\n- [x] y\n\n  ## Relations\t\n\n- related_to [[O]]\n"
    assert classify(note) == GROUPED


def test_a_heading_indented_four_spaces_is_code_not_a_group() -> None:
    assert classify(_body("    ### Indented four spaces\n\n- [x] y")) == FLAT


@pytest.mark.parametrize("opener", ["    ```", "``"])
def test_a_line_that_is_not_a_fence_does_not_hide_the_heading_below_it(opener: str) -> None:
    # Over-indented, or too short a run: neither opens a fence, so the group
    # heading under it is a group heading.
    assert classify(_body(f"{opener}\n### A real group\n\n- [x] y")) == GROUPED


def test_a_tab_after_the_hashes_still_opens_a_group() -> None:
    assert classify(_body("###\tTab separated\n\n- [x] y")) == GROUPED


def test_seven_hashes_are_not_a_heading_at_all() -> None:
    # Unreachable through `classify`, which only ever asks whether the level is
    # 3 -- but `_heading_level` mirrors basic-memory's predicate, and upstream
    # reads the answer as a level to compare, where 7 and "not a heading" differ.
    assert _heading_level("####### Seven hashes") is None
    assert _heading_level("###### Six hashes") == 6


def test_the_body_starts_at_observations_even_where_relations_comes_first() -> None:
    # On a note whose sections are the other way round, the Observations body is
    # what follows `## Observations` -- a `## Relations` above it does not close
    # a section that has not opened.
    note = "## Relations\n\n- related_to [[Other]]\n\n## Observations\n\n### A group\n\n- [x] y\n"
    assert classify(note) == GROUPED


def test_a_fenced_anchor_does_not_end_the_section_before_the_real_one() -> None:
    # The section runs to the `## Relations` the editor would resolve, which is
    # the unfenced one. Taking the fenced copy for the boundary would hide the
    # group below it and send the writer to mint a heading the note already has.
    note = (
        "## Observations\n\n- [definition] x #grounded\n\n~~~\n## Relations\n~~~\n\n"
        "### A later group\n\n- [insight] y #grounded\n\n## Relations\n\n- related_to [[O]]\n"
    )
    assert classify(note) == GROUPED


def test_a_fenced_anchor_does_not_start_the_section_before_the_real_one() -> None:
    # The same on the other anchor: the body starts at the `## Observations` the
    # editor resolves, so a `###` between the fenced copy and the real one is not
    # in the section.
    note = (
        "```\n## Observations\n```\n\n### Not in the section\n\n"
        "## Observations\n\n- [definition] x #grounded\n\n## Relations\n\n- related_to [[O]]\n"
    )
    assert classify(note) == FLAT


def test_a_heading_with_no_text_is_still_a_heading() -> None:
    # Mirror fidelity, like the seven-hashes case: upstream reads a bare `###`
    # as level 3, and `classify` calls a note whose only group heading is bare
    # `flat` if this branch goes -- sending the writer to mint a second heading
    # over claims that already sit under one.
    assert _heading_level("###") == 3
    assert classify(_body("###\n\n- [x] y")) == GROUPED


def test_a_tab_indented_heading_is_not_a_heading() -> None:
    # Upstream strips spaces only, so `\t### x` is not a heading to it and must not
    # be one here: reading it as a group would answer `grouped` for a note whose one
    # insight is unheaded, and the writer's wrap branch would never fire for it again.
    assert _heading_level("\t### Tab indented") is None
    assert classify(_body("\t### Tab indented\n\n- [definition] only insight #grounded")) == FLAT


def test_a_tab_indented_fence_does_not_open_a_fence() -> None:
    # Same rule on the fence marker: the indent is measured in spaces, so a tab
    # before the backticks leaves it text and the heading below it is a heading.
    assert classify(_body("\t```\n### A real group\n\n- [x] y")) == GROUPED


def test_a_line_separator_does_not_split_a_line() -> None:
    # The document is split on "\n", as the editor splits it. `str.splitlines`
    # also breaks on U+2028 and friends, which would find headings and anchors
    # where the editor resolves none.
    assert classify(_body("prose\u2028### Not a heading to the editor\n\n- [x] y")) == FLAT


def test_a_heading_indented_three_spaces_is_still_a_heading() -> None:
    # The other side of the four-space boundary. Upstream reads 0-3 leading spaces
    # as a heading and 4 as code; reading three as code would call a note whose
    # only group heading is indented `flat`, and the writer would mint a second
    # heading over claims that already sit under one.
    assert _heading_level("   ### Three spaces") == 3
    assert classify(_body("   ### Three spaces\n\n- [x] y")) == GROUPED


def test_a_fence_indented_three_spaces_still_opens_a_fence() -> None:
    # Same boundary on the fence marker: three spaces still opens one, so the
    # heading inside stays hidden and the note's one insight keeps its heading.
    assert classify(_body("   ```\n### Sample heading\n   ```")) == FLAT


def test_an_anchor_is_matched_on_its_leading_whitespace_too() -> None:
    # The other half of `line.strip()`. `rstrip` alone would read the indented
    # `## Relations` as no anchor, take the section to the end of the document,
    # and answer `grouped` for a note whose one insight has no heading.
    note = "## Observations\n\n- [definition] the only insight\n\n  ## Relations\n\n### Stray heading\n"
    assert classify(note) == FLAT
