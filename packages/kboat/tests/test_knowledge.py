"""The concept-note shape check, and the `kboat-knowledge` CLI over it.

The cases are written as whole notes rather than as fragments, because what the
check answers is about a note's layout and a fragment cannot be out of order or
end unclosed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kboat.knowledge.__main__ import _check_concepts, main
from kboat.knowledge.core import (
    BASE,
    BLOCKS,
    CODES,
    PASSES,
    UNCOVERED,
    ShapeFinding,
    _bullet_body,
    check_concept_note,
)

FLAT = """---
title: One
---

Lead.

## Observations

- [definition] a claim #grounded
- [source] An Article — <https://example.com/a>

## Relations

- related_to [[Other]]
"""

GROUPED = """---
title: Two
---

Lead.

## Observations

### What the first reading gave

- [definition] a claim #grounded
- [source] An Article — <https://example.com/a>

### What the second reading gave

- [insight] another claim #grounded
- [source] Another — <https://example.com/b>

## Relations

- related_to [[Other]]
"""


def codes(text: str) -> list[str]:
    return [f.code for f in check_concept_note(text, "N.md")]


def test_a_flat_note_and_a_grouped_note_are_both_clean() -> None:
    assert codes(FLAT) == []
    assert codes(GROUPED) == []


def test_a_finding_carries_its_disposition_and_repair_not_just_its_code() -> None:
    # The caller branches on the disposition and prints the repair, so neither is
    # something a skill has to restate — which is what kept going out of step.
    (finding,) = check_concept_note(FLAT.replace("## Relations", "## Later"), "N.md")
    out = finding.to_json()
    assert out["note"] == "N.md"
    assert out["code"] == "missing_section"
    assert out["detail"] == finding.detail
    assert out["disposition"] == BLOCKS
    assert out["repair"] == CODES["missing_section"].repair


# The disposition is the only thing any caller branches on: `base` skips the phases,
# `blocks` refuses that one note, `passes` owes nothing. Which code carries which is
# therefore behaviour, and the whole table is pinned rather than one row of it —
# `unclosed_claims` on `passes` alone would have `kboat-distill` write into a note
# holding unprovenanced claims and close them with its own `[source]` line, which is
# the failure this module exists to prevent.
DISPOSITIONS = {
    "missing_section": BLOCKS,
    "duplicate_section": BLOCKS,
    "sections_out_of_order": BLOCKS,
    "unclosed_claims": BLOCKS,
    "heading_parses_as_content": BLOCKS,
    "spare_headings": BLOCKS,
    "claim_outside_observations": BLOCKS,
    "heading_group_mismatch": BLOCKS,
    "note_outside_concepts": UNCOVERED,
    "unreadable_note": BLOCKS,
    "icloud_placeholder": BLOCKS,
    "empty_observations": PASSES,
    "lone_group_headed": PASSES,
    "no_knowledge_root": BASE,
    "no_concepts_dir": PASSES,
    "unreadable_dir": BASE,
}


def test_every_code_carries_the_disposition_its_caller_branches_on() -> None:
    assert {code: meaning.disposition for code, meaning in CODES.items()} == DISPOSITIONS


def test_a_code_with_no_entry_fails_where_it_is_made() -> None:
    # Not at the caller, which would have no disposition to branch on.
    with pytest.raises(ValueError, match="unknown code"):
        ShapeFinding("n.md", "invented_code", "x")


def test_every_disposition_is_one_of_the_three() -> None:
    assert {c.disposition for c in CODES.values()} == {BASE, BLOCKS, PASSES, UNCOVERED}


def test_every_code_the_check_can_emit_has_an_entry() -> None:
    # `__post_init__` enforces it at construction, so this pins the reverse: the
    # table carries nothing the modules cannot produce, and so stays readable as
    # the whole set rather than drifting into a superset.
    source = (
        Path(__file__).resolve().parent.parent / "src/kboat/knowledge/core.py"
    ).read_text() + (
        Path(__file__).resolve().parent.parent / "src/kboat/knowledge/__main__.py"
    ).read_text()
    for code in CODES:
        assert f'"{code}"' in source, code


@pytest.mark.parametrize("heading", ["## Observations", "## Relations"])
def test_either_section_missing_is_reported(heading: str) -> None:
    assert "missing_section" in codes(FLAT.replace(heading, "## Elsewhere"))


def test_a_note_missing_observations_is_not_scanned_further() -> None:
    # Nothing to scan, and reporting an "empty" observations block for a note
    # that has no such section would name the wrong repair.
    assert codes(FLAT.replace("## Observations", "## Elsewhere")) == ["missing_section"]


def test_a_duplicated_section_is_reported_because_the_insert_raises_on_it() -> None:
    assert "duplicate_section" in codes(FLAT + "\n## Relations\n")


def test_relations_standing_above_observations_is_reported() -> None:
    swapped = """---
title: Three
---

## Relations

- related_to [[Other]]

## Observations

- [definition] a claim #grounded
- [source] An Article — <https://example.com/a>
"""
    assert codes(swapped) == ["sections_out_of_order"]


def test_an_empty_observations_section_is_reported() -> None:
    assert codes("## Observations\n\n## Relations\n") == ["empty_observations"]


def test_claims_with_no_source_line_at_all_are_unclosed() -> None:
    assert codes(FLAT.replace("- [source] An Article — <https://example.com/a>\n", "")) == [
        "unclosed_claims"
    ]


def test_a_claim_trailing_the_last_source_line_is_unclosed() -> None:
    trailing = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n- [insight] orphan #grounded",
    )
    assert codes(trailing) == ["unclosed_claims"]


def test_a_lone_group_carrying_a_heading_is_reported() -> None:
    assert codes(FLAT.replace("## Observations\n", "## Observations\n\n### A heading\n")) == [
        "lone_group_headed"
    ]


def test_a_note_nobody_converted_is_counted_by_its_source_runs() -> None:
    # Two groups and no heading anywhere: counting headings would read this as
    # the single flat group it is allowed to be.
    never = GROUPED.replace("### What the first reading gave\n\n", "").replace(
        "### What the second reading gave\n\n", ""
    )
    assert codes(never) == ["heading_group_mismatch"]


def test_a_heading_left_over_after_the_groups_is_reported() -> None:
    # It heads nothing, so it is spare rather than a count mismatch — and
    # `spare_headings`' repair, "take the spare headings off", is what to do with it.
    surplus = GROUPED.replace("## Relations", "### Stranded\n\n## Relations")
    assert codes(surplus) == ["spare_headings"]


def test_two_departures_are_both_reported() -> None:
    # The worse of the two must not sit masked behind the lesser until whoever
    # ran this happens to run it again.
    both = """---
title: Four
---

## Relations

## Observations

- [definition] a claim #grounded
"""
    assert codes(both) == ["sections_out_of_order", "unclosed_claims"]


def test_a_source_run_of_two_lines_closes_one_group() -> None:
    joint = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n- [source] Another — <https://example.com/b>",
    )
    assert codes(joint) == []


def _root(tmp_path: Path, **notes: str) -> Path:
    concepts = tmp_path / "concepts"
    concepts.mkdir()
    for name, text in notes.items():
        (concepts / f"{name}.md").write_text(text, encoding="utf-8")
    return tmp_path


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_a_clean_root_reports_its_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _root(tmp_path, one=FLAT, two=GROUPED)
    code, report = _run(["--knowledge", str(root)], capsys)
    assert code == 0
    assert report["checked"] == 2
    # Reported at zero, so a clean base is distinguishable from a check that never ran.
    assert report["counts"] == {"total": 0, "by_code": {}}


def test_findings_are_report_only_until_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, bad="## Observations\n\n## Relations\n")
    assert _run(["--knowledge", str(root)], capsys)[0] == 0
    code, report = _run(["--knowledge", str(root), "--strict"], capsys)
    assert code == 1
    assert report["counts"] == {"total": 1, "by_code": {"empty_observations": 1}}
    assert report["findings"][0]["note"] == "concepts/bad.md"


def test_the_root_comes_from_the_environment_when_the_flag_is_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path, one=FLAT)
    monkeypatch.setenv("KBOAT_KNOWLEDGE_PATH", str(root))
    assert _run([], capsys)[1]["checked"] == 1


def test_no_root_at_all_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KBOAT_KNOWLEDGE_PATH", raising=False)
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_a_concepts_directory_that_cannot_be_listed_is_reported_not_read_as_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, one=FLAT)
    (root / "concepts").chmod(0o600)
    try:
        code, report = _run(["--knowledge", str(root), "--strict"], capsys)
    finally:
        (root / "concepts").chmod(0o700)
    assert code == 1
    assert report["checked"] == 0
    assert [f["code"] for f in report["findings"]] == ["unreadable_dir"]


def test_a_root_with_no_concepts_directory_is_a_finding_not_a_clean_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The failure this guards is a wrong root, not a wrong note: zero notes and
    # zero findings is exactly what a clean base looks like, and this is the only
    # check there is over the knowledge root.
    code, report = _run(["--knowledge", str(tmp_path), "--strict"], capsys)
    assert code == 1
    assert report["checked"] == 0
    assert [f["code"] for f in report["findings"]] == ["no_concepts_dir"]


def test_a_note_that_cannot_be_read_is_reported_against_its_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, one=FLAT, two=FLAT)
    (root / "concepts" / "two.md").chmod(0o000)
    try:
        code, report = _run(["--knowledge", str(root), "--strict"], capsys)
    finally:
        (root / "concepts" / "two.md").chmod(0o600)
    assert code == 1
    assert report["checked"] == 2
    assert [(f["note"], f["code"]) for f in report["findings"]] == [
        ("concepts/two.md", "unreadable_note")
    ]


def test_an_evicted_note_is_reported_rather_than_silently_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, one=FLAT)
    (root / "concepts" / ".gone.md.icloud").write_text("", encoding="utf-8")
    code, report = _run(["--knowledge", str(root), "--strict"], capsys)
    assert code == 1
    assert [(f["note"], f["code"]) for f in report["findings"]] == [
        ("concepts/.gone.md.icloud", "icloud_placeholder")
    ]


def test_the_finding_type_is_hashable_and_comparable() -> None:
    a = ShapeFinding("n.md", "empty_observations", "x")
    assert a == ShapeFinding("n.md", "empty_observations", "x")
    assert len({a, ShapeFinding("n.md", "empty_observations", "x")}) == 1


# The four below pin boundaries the rest of the suite leaves free: each was found
# by mutating `core.py` and watching every other test stay green.


def test_the_group_scan_stops_at_the_next_section() -> None:
    # A bullet past `## Observations` is not one of this note's groups: without the
    # boundary a claim-shaped one leaves the section reading as unclosed and a
    # source-shaped one adds a group. It is still reported — see the test below —
    # but as a claim outside the groups rather than as part of them.
    for spill in (
        "- [insight] a stray claim #grounded",
        "- [source] Elsewhere — <https://example.com/z>",
    ):
        assert codes(FLAT + f"\n## Later\n\n{spill}\n") == ["claim_outside_observations"], spill


@pytest.mark.parametrize(
    ("tail", "why"),
    [
        ("## Notes\n\n- [insight] a claim nothing closes #grounded", "under a later section"),
        ("- [insight] a claim nothing closes #grounded", "appended past `## Relations`"),
    ],
)
def test_a_claim_the_graph_holds_outside_the_groups_is_reported(tail: str, why: str) -> None:
    # `observation_rule` walks the whole document with no section awareness, so a
    # scan reading `## Observations` alone calls a note clean while the base holds a
    # claim with no `[source]` line and no reading behind it. The second row is what
    # `append` produces — the operation the writer is told twice never to use.
    assert codes(FLAT + f"\n{tail}\n") == ["claim_outside_observations"], why


def test_an_ordinary_relation_line_is_not_a_stray_claim() -> None:
    # The graph reads a relation, not an observation, out of `- related_to [[X]]`,
    # so the note every run writes must stay clean.
    assert codes(FLAT) == []


def test_a_section_heading_must_be_the_whole_line() -> None:
    # `## Observations and notes` is a different section, so this note has none.
    renamed = FLAT.replace("## Observations", "## Observations and notes")
    assert "missing_section" in codes(renamed)


def test_only_a_level_three_heading_opens_a_group() -> None:
    # A deeper heading is a heading to the graph — its text is checked like any
    # other — but it is not what `kboat-notes` writes for a reading group, so it is
    # not counted as one.
    for line in ("#### A deeper heading", "###### deeper still"):
        assert codes(FLAT.replace("## Observations\n", f"## Observations\n\n{line}\n")) == [], line


@pytest.mark.parametrize(
    ("heading", "why"),
    [
        ("###  two spaces before the text", "the delimiter is a run, however long"),
        ("###\ta tab before the text", "a tab delimits it too"),
        ("### a closing run of hashes ###", "which markdown-it strips from the text"),
    ],
)
def test_a_heading_markdown_accepts_is_one_here_too(heading: str, why: str) -> None:
    # Written with a pattern of this module's own, these were invisible: the group
    # count came back short and the note read `heading_group_mismatch` — blocks —
    # against a note whose headings are all where they belong.
    ordinary = GROUPED.replace("### What the first reading gave", heading)

    assert codes(ordinary) == [], why


def test_a_closing_run_of_hashes_is_not_part_of_the_heading_text() -> None:
    # Taken as text it would be a `#`-prefixed word, so the heading read as a claim
    # the graph holds — `heading_parses_as_content`, blocks — when the graph holds
    # nothing from it.
    assert codes(GROUPED.replace("### What the first reading gave", "### A heading ###")) == []


@pytest.mark.parametrize("heading", ["## Notes on #ml", "## See also [[KV cache]]"])
def test_a_heading_at_the_block_boundary_is_read_too(heading: str) -> None:
    # The heading that *ends* `## Observations` sits at the boundary index, and the
    # stray scan's range has to take it: standing between the two sections it is
    # both misplaced and indexed, and reporting only the misplacement leaves the
    # claim or the edge in the graph after the human moves the section.
    between = FLAT.replace("## Relations", f"{heading}\n\n## Relations")

    assert codes(between) == ["sections_out_of_order", "heading_parses_as_content"]


@pytest.mark.parametrize("heading", ["## Notes on #ml", "## See also [[KV cache]]"])
def test_a_level_one_or_two_heading_outside_the_groups_is_still_read(heading: str) -> None:
    # The graph reads every heading level. Taking only three through six left a
    # later `## ` section minting a claim or an edge with nothing reporting it — and
    # a heading gets the heading's repair, not the bullet's: "move it under
    # `## Observations`" would put it exactly where `heading_parses_as_content`
    # blocks on it.
    assert codes(FLAT + f"\n{heading}\n") == ["heading_parses_as_content"]


def test_the_order_check_reads_the_first_of_each_section() -> None:
    # A duplicated `## Relations` straddling `## Observations` is still out of
    # order; comparing the last of each would call it fine.
    straddled = """---
title: Five
---

## Relations

## Observations

- [definition] a claim #grounded
- [source] An Article — <https://example.com/a>

## Relations

- related_to [[Other]]
"""
    assert "sections_out_of_order" in codes(straddled)


def test_a_root_the_os_refuses_is_unreadable_rather_than_absent() -> None:
    # The two codes send the human to different places: one says the path is
    # wrong, the other says the path is right and the access is not. The refusal is
    # met at the `concepts` probe rather than at the root's own — `root.stat()`
    # goes through the parent and succeeds on a `0o600` directory — so the `note`
    # is asserted as well as the code, or the test would pass off either branch.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        (root / "concepts").mkdir(parents=True)
        root.chmod(0o600)
        try:
            checked, findings = _check_concepts(root)
        finally:
            root.chmod(0o700)
    assert (checked, [(f.note, f.code) for f in findings]) == (0, [("concepts", "unreadable_dir")])


def test_a_file_where_the_concepts_directory_should_be_is_reported(tmp_path: Path) -> None:
    # With a stray beside it, as the sibling branch's case has: without the strays
    # the verdict turns on whether an empty directory exists rather than on the base,
    # and a root holding notes reads as a fresh install.
    (tmp_path / "concepts").write_text("not a directory", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "a.md").write_text(FLAT, encoding="utf-8")

    checked, findings = _check_concepts(tmp_path)

    assert (checked, sorted(f.code for f in findings)) == (
        0,
        ["no_concepts_dir", "note_outside_concepts"],
    )


def test_an_unclosed_run_does_not_mask_a_heading_mismatch() -> None:
    # Two closed groups, one heading, and a dangling claim: both are true of the
    # note and a curator who saw one would come back for the other.
    both = GROUPED.replace("### What the second reading gave\n\n", "").replace(
        "- [source] Another — <https://example.com/b>",
        "- [source] Another — <https://example.com/b>\n- [insight] orphan #grounded",
    )
    assert codes(both) == ["unclosed_claims", "heading_group_mismatch"]


def test_a_claim_basic_memory_indexes_without_a_category_is_still_a_claim() -> None:
    # `is_observation` reads a bullet carrying a `#`-word as an observation even
    # with no `[category]`, so an uncategorised one left after the last `[source]`
    # line is an indexed claim with no provenance.
    orphan = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n- an uncategorised claim #grounded",
    )
    assert codes(orphan) == ["unclosed_claims"]


@pytest.mark.parametrize("bullet", ["*", "+"])
def test_the_other_list_markers_carry_observations_too(bullet: str) -> None:
    other = FLAT.replace("\n- [", f"\n{bullet} [")
    assert codes(other) == []


@pytest.mark.parametrize(
    "bullet",
    [
        # Each of these reaches the exclusion that names it and would be read as a
        # claim without it, so the case carries the exclusion rather than passing
        # for some other reason: the task markers would parse as a category of
        # whitespace, the link and the wikilink carry a `#`-word that the has-tag
        # test would otherwise catch, and the trailing text after a leading
        # wikilink is what the category's bracket-excluding character class buys.
        "- [ ] a task",
        "- [x] a done task",
        "- [-] a dropped task",
        "- [Read it #ml](https://example.com)",
        "- [[Another #ml]]",
        "- [[KV cache]] grows linearly",
    ],
)
def test_what_basic_memory_does_not_index_is_not_a_claim(bullet: str) -> None:
    inside = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        f"- [source] An Article — <https://example.com/a>\n{bullet}",
    )
    assert codes(inside) == []


# Every row is `is_observation` in the installed basic-memory (0.23.0,
# `markdown/plugins.py`), read out of the uv cache and run against this predicate.
# The point of the module is to know what the graph holds, so a rule of our own —
# narrower or wider — reports on a base that does not exist.
MIRRORS = [
    ("- [insight] a claim #grounded", True, "the ordinary claim"),
    ("- [source] An Article — <https://example.com/a>", True, "the ordinary provenance line"),
    ("- [>] deferred idea", False, "extended task vocabulary, not a category"),
    ("- [?] an open question", False, "same"),
    ("- [X] done", False, "same, uppercase"),
    ("- [/] in progress", False, "same"),
    ("- [00:12:30] the speaker says something", False, "a transcript timecode, not a category"),
    ("- [1:02:03.500] and again", False, "same, with milliseconds"),
    ("- [source] [An Article](https://example.com/a)", False, "the link exclusion swallows it"),
    ("- [insight] see [the docs](https://example.com/x)", False, "and any claim shaped like one"),
    (
        "- [insight] see [the docs](https://example.com/x) #grounded",
        True,
        "unless it does not end in `)`",
    ),
    ("- [x] a task", False, "the checkbox markers upstream excludes"),
    ("- [[Bare wikilink]]", False, "a bare wikilink is not a claim"),
    ("- just an ordinary bullet", False, "no category, no tag"),
]


@pytest.mark.parametrize(("bullet", "indexed", "why"), MIRRORS)
def test_the_predicate_mirrors_what_basic_memory_indexes(
    bullet: str, indexed: bool, why: str
) -> None:
    assert (_bullet_body(bullet) is not None) is indexed, why


def test_a_source_line_the_graph_does_not_index_leaves_its_group_unclosed() -> None:
    # The consequence of mirroring, and the reason narrowing the link exclusion is
    # the wrong repair: written this way the provenance line is not in the graph
    # either, so the group's claims really do stand there without one. Reporting the
    # note clean would be the false answer, not this.
    linked = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] [An Article](https://example.com/a)",
    )

    assert codes(linked) == ["unclosed_claims"]


def test_a_plain_bullet_carrying_neither_a_category_nor_a_tag_is_not_a_claim() -> None:
    # Basic Memory indexes nothing from it, so it leaves the section closed.
    plain = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n- just an ordinary bullet",
    )
    assert codes(plain) == []


def test_a_note_that_is_not_utf8_is_one_finding_and_not_a_dead_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `UnicodeDecodeError` is a `ValueError`, so without the widened boundary it
    # escapes and the run produces no report at all — which a caller reads as a
    # root it could not reach, and which skips the phases for one bad note.
    root = _root(tmp_path, good=FLAT)
    (root / "concepts" / "bad.md").write_bytes(b"## Observations\n\n- [x] \xff\n")
    code, report = _run(["--knowledge", str(root), "--strict"], capsys)
    assert code == 1
    assert report["checked"] == 2
    assert [(f["note"], f["code"]) for f in report["findings"]] == [
        ("concepts/bad.md", "unreadable_note")
    ]


@pytest.mark.parametrize(
    "bullet",
    [
        # Each reaches a boundary of Basic Memory's predicate that the cases above
        # leave free: the category class excludes parentheses, a category with
        # nothing after it is not an observation, a `#` mid-token is not a tag
        # (`is_observation`'s own reason is `color="#4285F4"`), and `[source]` has
        # to open the bullet rather than appear anywhere in it.
        "- [note (2024)] a claim",
        "- [insight]",
        '- color="#4285F4" and nothing else',
    ],
)
def test_the_predicate_boundaries_match_basic_memory(bullet: str) -> None:
    inside = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        f"- [source] An Article — <https://example.com/a>\n{bullet}",
    )
    assert codes(inside) == []


def test_a_claim_mentioning_the_source_marker_does_not_close_its_group() -> None:
    trailing = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n"
        "- [insight] the [source] line closes a group #grounded",
    )
    assert codes(trailing) == ["unclosed_claims"]


def test_a_note_in_a_subtopic_folder_is_still_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `memory-curate` proposes grouping a crowded folder into subfolders, so a
    # flat scan would shrink the moment a human took that advice — and a note it
    # stopped seeing would read to `kboat-distill` as a note with nothing wrong.
    root = _root(tmp_path, good=FLAT)
    # Two levels, not one: a single level is reached by a flat `glob` as well, so a
    # fixture that shallow pins the existence of the walk and not its depth.
    deep = root / "concepts" / "subtopic" / "deeper"
    deep.mkdir(parents=True)
    (deep / "bad.md").write_text(
        "## Observations\n\n- [insight] a claim #grounded\n\n## Relations\n", encoding="utf-8"
    )
    code, report = _run(["--knowledge", str(root), "--strict"], capsys)
    assert code == 1
    assert report["checked"] == 2
    assert [(f["note"], f["code"]) for f in report["findings"]] == [
        ("concepts/subtopic/deeper/bad.md", "unclosed_claims")
    ]


def test_a_note_outside_concepts_is_reported_rather_than_passed_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, good=FLAT)
    (root / "stray.md").write_text(FLAT, encoding="utf-8")
    (root / "elsewhere").mkdir()
    (root / "elsewhere" / "also.md").write_text(FLAT, encoding="utf-8")
    code, report = _run(["--knowledge", str(root), "--strict"], capsys)
    assert code == 1
    assert sorted(f["note"] for f in report["findings"]) == ["elsewhere/also.md", "stray.md"]
    assert {f["code"] for f in report["findings"]} == {"note_outside_concepts"}


def test_meta_notes_are_not_concept_notes_and_are_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A hub or vocabulary note follows none of the concept-note rules (kboat-notes):
    # this one has no `## Relations` and no `[source]` line at all.
    root = _root(tmp_path, good=FLAT)
    (root / "meta").mkdir()
    (root / "meta" / "Tag vocabulary.md").write_text(
        "## Observations\n\n- [convention] reuse-first #meta\n", encoding="utf-8"
    )
    code, report = _run(["--knowledge", str(root), "--strict"], capsys)
    assert (code, report["checked"], report["findings"]) == (0, 1, [])


def test_a_heading_the_graph_reads_as_an_observation_is_reported() -> None:
    # This change is what put headings in these notes at all, and `kboat-notes`
    # forbids three things in one. A `#`-prefixed word makes the heading itself an
    # observation, which lands in the graph with no `[source]` closing it — the
    # state `unclosed_claims` exists for, arriving from the one place a reader
    # checking provenance would not think to look.
    bad = GROUPED.replace("### What the first reading gave", "### Why #KVcache sizing matters")

    assert codes(bad) == ["heading_parses_as_content"]


def test_a_heading_opening_with_a_category_is_reported() -> None:
    # The second of the three: a leading `[...]` reads as an observation category.
    bad = GROUPED.replace("### What the first reading gave", "### [insight] what the reading gave")

    assert codes(bad) == ["heading_parses_as_content"]


def test_a_heading_carrying_a_wikilink_is_reported() -> None:
    # The third, and the one that leaves no claim behind to notice: the relation
    # parser turns it into a `links_to` edge the graph carries as though a bullet
    # had declared it.
    bad = GROUPED.replace("### What the first reading gave", "### What [[KV cache]] sizing implies")

    assert codes(bad) == ["heading_parses_as_content"]


def test_an_ordinary_heading_is_not_reported() -> None:
    # The headings this change actually writes carry none of the three, so the
    # check must stay silent on them or every converted note would block.
    assert codes(GROUPED) == []


def test_an_indented_section_heading_is_the_anchor_basic_memory_writes_against() -> None:
    # Basic Memory matches `line.strip() == header.strip()` (`note_preparation.py`),
    # so an indented heading is a section it inserts into without complaint. Reading
    # it as missing would block a note the writer can use, and take the relations
    # bullets into the observations scan on the way.
    indented = FLAT.replace("## Relations", "  ## Relations")

    assert codes(indented) == []


def test_a_heading_inside_a_fence_is_not_a_section() -> None:
    # `kboat-notes` tells writers to put a multi-line formula in a fenced block, and
    # Basic Memory skips fenced lines when it looks for an anchor. Counting one would
    # report `duplicate_section` — blocks — against a note that has no duplicate.
    fenced = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n\n```text\n## Relations\n```",
    )

    assert codes(fenced) == []


def test_two_headings_over_one_group_is_not_the_state_the_next_append_absorbs() -> None:
    # One heading over one group is an interrupted conversion and the next append
    # settles it. Two is not: the append makes three headings over two groups, which
    # is `heading_group_mismatch` — blocks — so the repair must not say "leave it".
    two = GROUPED.replace(
        "- [insight] another claim #grounded\n- [source] Another — <https://example.com/b>\n",
        "",
    )
    assert two.count("### ") == 2, "one group, two headings — the state a curation pass leaves"

    assert codes(two) == ["spare_headings"], "not the state the next append absorbs"
    assert CODES["spare_headings"].disposition == BLOCKS


def test_a_fence_inside_the_observations_block_does_not_end_it() -> None:
    # `kboat-notes` prescribes a fenced block for a multi-line formula, and one
    # holding a `## ` line would truncate the section — dropping the `[source]` line
    # below it and reporting `unclosed_claims`, which blocks, against a note whose
    # group is closed.
    with_formula = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "```text\n## not a heading\nx = 1\n```\n\n- [source] An Article — <https://example.com/a>",
    )

    assert codes(with_formula) == []


def test_a_heading_the_graph_indexes_is_reported_even_with_no_bullets_yet() -> None:
    # `empty_observations` suppresses the *counts* below it, and the heading scan is
    # not one of them: the heading puts its claim in the graph whether or not a
    # bullet ever follows. Left at `passes`, the next pass is free to write, and its
    # `[source]` line closes a claim no source made — the state `unclosed_claims`
    # exists to prevent — after which the note turns `blocks` and aborts every
    # source needing that concept.
    empty = FLAT.replace(
        "- [definition] a claim #grounded\n- [source] An Article — <https://example.com/a>",
        "### Why #KVcache sizing matters",
    )

    assert set(codes(empty)) == {"empty_observations", "heading_parses_as_content"}


@pytest.mark.parametrize(
    "heading",
    [
        "### Why #KVcache sizing matters",
        "  ### Why #KVcache sizing matters",
        "#### Why #KVcache sizing matters",
        "  ### What [[KV cache]] sizing implies",
    ],
)
def test_every_heading_shape_the_graph_indexes_is_reported(heading: str) -> None:
    # Basic Memory reads a heading's text as an inline whatever its level and
    # whatever its indent (up to three spaces), so a check that saw only a column-0
    # `###` left the claim in the graph through every other shape — which is the
    # state this code exists to prevent, arriving where nobody looks for it.
    bad = GROUPED.replace("### What the first reading gave", heading)

    assert "heading_parses_as_content" in codes(bad), heading


def test_a_tilde_fence_hides_a_heading_as_a_backtick_fence_does() -> None:
    # Basic Memory's fence detector takes `~~~` too. Missing it reports
    # `duplicate_section` — blocks — against a note it reads as having one
    # `## Relations` and writes into happily, and `kboat-notes` prescribes a fenced
    # block for a multi-line formula without naming the marker.
    fenced = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n\n~~~text\n## Relations\n~~~",
    )

    assert codes(fenced) == []


def test_a_backtick_fence_whose_info_string_holds_a_backtick_opens_nothing() -> None:
    # The other direction: Basic Memory does not open a fence there, so the lines
    # below stay ordinary — and a check that hid them would stop seeing the group's
    # own `[source]` line.
    text = FLAT.replace(
        "## Observations\n",
        "## Observations\n\n```js `x`\n",
    )

    assert codes(text) == [], "the group's own bullets must stay visible"


@pytest.mark.parametrize(
    ("heading", "flagged"),
    [
        ("[ ] a draft heading", False),
        ("[Read it #ml](https://example.com)", False),
        ("[x] revisit this", False),
        ("[insight] what the reading gave", True),
    ],
)
def test_a_heading_takes_the_same_exclusions_a_bullet_does(heading: str, flagged: bool) -> None:
    # `observation_rule` walks every inline, so the exclusions are not a list-item
    # rule. Applied to bullets alone, a heading the graph reads nothing from came
    # back `heading_parses_as_content` — blocks — with a `detail` saying the graph
    # reads it as content, which is what the human is then sent to repair.
    note = GROUPED.replace("### What the first reading gave", f"### {heading}")

    assert ("heading_parses_as_content" in codes(note)) is flagged, heading


def test_a_fence_marker_indented_past_three_spaces_is_not_a_fence() -> None:
    # One of the four the mirror's docstring names. Read as a fence, the `## `
    # below it is hidden and `## Relations` goes missing — blocks — on a note
    # Basic Memory reads as having one.
    text = FLAT.replace("## Relations", "    ```\n\n## Relations")

    assert codes(text) == []


def test_a_closing_fence_shorter_than_its_opener_does_not_close_it() -> None:
    # The fourth. Read as closing, the lines below re-enter the document and a
    # `## Relations` inside the block is counted a second time.
    text = FLAT + "\n````text\n```\n## Relations\n````\n"

    assert codes(text) == []


def test_a_group_that_lost_its_source_line_is_reported_where_it_sits() -> None:
    # Counting only the tail let an interior open run be absorbed by the next
    # group's provenance line, so the report came back `heading_group_mismatch` —
    # whose repair is "drop any heading left over". Following it makes the note
    # check clean with a claim now reading as supported by a source that never
    # made it, which is the state `unclosed_claims` exists to refuse.
    lost = GROUPED.replace(
        "### What the second reading gave\n\n- [insight] another claim #grounded\n",
        "### What the second reading gave\n\n- [insight] another claim #grounded\n"
        "\n### What the third reading gave\n\n- [insight] a third claim #grounded\n",
    )

    assert "unclosed_claims" in codes(lost)


def test_a_root_with_no_concepts_folder_yet_is_not_a_root_to_fix(tmp_path: Path) -> None:
    # The folder is created by the first `write_note(directory="concepts")`, so a
    # `base` verdict here would stop the one thing that makes it — a fresh install
    # distilling nothing for ever while the report blames the path.
    (finding,) = _check_concepts(tmp_path)[1]

    assert finding.code == "no_concepts_dir"
    assert CODES[finding.code].disposition == PASSES


def test_a_root_that_is_not_there_is_a_root_to_fix(tmp_path: Path) -> None:
    (finding,) = _check_concepts(tmp_path / "gone")[1]

    assert finding.code == "no_knowledge_root"
    assert CODES[finding.code].disposition == BASE


def test_an_indented_relations_heading_ends_the_observations_block() -> None:
    # `_section_lines` accepts an indented heading because Basic Memory anchors on
    # it; the block boundary has to agree, or the observations scan runs on through
    # the relation bullets below. With an indexed one down there the note came back
    # `unclosed_claims` — blocks, and the repair sends a human after unprovenanced
    # claims that do not exist — where what it holds is a claim outside the groups.
    indented = FLAT.replace(
        "## Relations\n\n- related_to [[Other]]",
        "  ## Relations\n\n- related_to [[Other]] (contrast with #ml)",
    )

    assert codes(indented) == ["claim_outside_observations"]


@pytest.mark.parametrize(
    "lead",
    [
        "~10% of the KV cache is wasted on padding.",
        "``x`` is the tensor",
        # The one that reaches the floor: two tildes are ordinary strikethrough, and
        # the other two rows are rejected before the length is ever looked at.
        "~~The earlier estimate~~ is superseded.",
    ],
)
def test_a_body_line_opening_with_one_or_two_fence_characters_is_not_a_fence(lead: str) -> None:
    # The three-character floor is the fifth thing the mirror gets right and the one
    # deciding whether an ordinary sentence is a fence at all. Without it the line
    # opens a phantom fence that swallows both section anchors, and the note comes
    # back missing them — blocks, twice, on a note with nothing wrong.
    assert codes(FLAT.replace("Lead.", lead)) == [], lead


def test_a_knowledge_root_that_is_a_file_is_a_root_to_fix(tmp_path: Path) -> None:
    # Without the directory test the run falls through to the `concepts` branch and
    # answers `no_concepts_dir` — `passes` — so the phases run, no notification
    # fires, and the report reads 0 checked with 0 findings, which is what a clean
    # base looks like. Reachable from a stale `KBOAT_KNOWLEDGE_PATH`.
    root = tmp_path / "kboat"
    root.write_text("not a directory\n", encoding="utf-8")

    (finding,) = _check_concepts(root)[1]

    assert finding.code == "no_knowledge_root"
    assert finding.note == "."


def test_a_deeper_heading_inside_a_group_does_not_close_its_run() -> None:
    # A group boundary is what `kboat-notes` writes as `###`; a deeper heading is an
    # aside inside a group and no rule forbids one. Treating it as a boundary
    # reported the group as unclosed with its own `[source]` line two lines below —
    # `blocks`, beside a contradictory `passes`, and a repair sending a human after
    # claims that have provenance.
    aside = GROUPED.replace(
        "- [definition] a claim #grounded\n",
        "- [definition] a claim #grounded\n\n#### an aside\n\n",
    )

    assert codes(aside) == []


def test_a_line_of_seven_hashes_is_not_a_heading() -> None:
    # markdown-it and the mirrored predicate both stop at six. Read as a heading it
    # would close the run above it — `unclosed_claims`, blocks — and have its text
    # scanned for a claim.
    seven = GROUPED.replace(
        "- [definition] a claim #grounded\n",
        "- [definition] a claim #grounded\n\n####### not a heading #ml\n\n",
    )

    assert codes(seven) == []


def test_a_knowledge_root_the_os_will_not_stat_is_unreadable_not_absent(tmp_path: Path) -> None:
    # `root.stat()` goes through the *parent*, so this branch is reached only when
    # the parent refuses. Reported as absent, it would send a human to change a path
    # that was right all along.
    parent = tmp_path / "outer"
    (parent / "kboat" / "concepts").mkdir(parents=True)
    parent.chmod(0o600)
    try:
        checked, findings = _check_concepts(parent / "kboat")
    finally:
        parent.chmod(0o700)

    assert (checked, [(f.note, f.code) for f in findings]) == (0, [(".", "unreadable_dir")])


def test_a_root_holding_notes_outside_concepts_is_not_a_fresh_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Without the strays the verdict turned on whether an empty `concepts/` exists,
    # not on the base: a `KBOAT_KNOWLEDGE_PATH` pointed one level off answered
    # `no_concepts_dir` at `passes`, so the pass ran and started a second, empty
    # base beside the notes it could not see, with nothing notifying.
    (tmp_path / "concept-notes").mkdir()
    (tmp_path / "concept-notes" / "a.md").write_text(FLAT, encoding="utf-8")

    code, report = _run(["--knowledge", str(tmp_path), "--strict"], capsys)

    assert code == 1
    assert sorted(f["code"] for f in report["findings"]) == [
        "no_concepts_dir",
        "note_outside_concepts",
    ]


def test_a_heading_over_claims_that_close_no_group_is_reported() -> None:
    # `groups == 0` with a heading: the claims never meet a `[source]` line, so no
    # group closes under the heading. Left off the count chain the note read clean,
    # and the next append made it `spare_headings` — after that write had landed.
    no_group = FLAT.replace(
        "- [definition] a claim #grounded\n- [source] An Article — <https://example.com/a>",
        "### The reading\n\n- [definition] a claim #grounded",
    )

    assert codes(no_group) == ["unclosed_claims", "spare_headings"]


def test_a_claim_less_group_does_not_swallow_the_next_one() -> None:
    # The `[source]` run has to end at a group heading as the claim run does, or the
    # next group's provenance reads as a continuation of the last one's and the two
    # count as one — reporting `spare_headings` at `blocks` on a note shaped exactly
    # as `kboat-notes` prescribes. Following *that* repair merges the groups and
    # makes the surviving source vouch for a claim it never made.
    two = GROUPED.replace("- [insight] another claim #grounded\n", "")

    assert codes(two) == []


def test_a_provenance_line_with_no_claims_above_it_still_closes_a_group() -> None:
    # A reading that contributed nothing is still a reading. Counted as no group at
    # all, such a note matched no branch and read clean — until the writer took the
    # "observations and no `###`" shape, put a heading over it, and made the note
    # `spare_headings`, after that write had landed.
    provenance_only = FLAT.replace("- [definition] a claim #grounded\n", "")
    assert codes(provenance_only) == []

    # And the state the writer leaves next: a heading over that group and its own
    # group beside it. Counted as no group, this is two headings over one and comes
    # back `spare_headings` — blocks — with the write already landed.
    appended = provenance_only.replace(
        "- [source] An Article — <https://example.com/a>",
        "### The first reading\n\n- [source] An Article — <https://example.com/a>\n\n"
        "### The second\n\n- [insight] a claim #grounded\n- [source] Another — <https://example.com/b>",
    )

    assert codes(appended) == []


def test_a_fenced_group_heading_and_bullet_inside_the_block_are_masked() -> None:
    # `kboat-notes` prescribes a fenced block for a multi-line formula, which sits
    # inside a group. Read as content it adds a heading and an open run, so the note
    # comes back `unclosed_claims` and `lone_group_headed` instead of clean.
    formula = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "```text\n### not a heading\n- [insight] not a claim #grounded\n```\n\n"
        "- [source] An Article — <https://example.com/a>",
    )

    assert codes(formula) == []


def test_a_bullet_body_is_read_without_its_leading_whitespace() -> None:
    # Basic Memory strips the token's content, so `-  [source] …` is a provenance
    # line to the graph. Left unstripped it is not one here, and the group it closes
    # reads as unclosed — `blocks`, against a note the graph is happy with.
    spaced = FLAT.replace("- [source] An Article", "-  [source] An Article")

    assert codes(spaced) == []


def test_a_section_standing_between_the_two_takes_the_reading_group() -> None:
    # A group is inserted *before* `## Relations`, anchored on that line, so the two
    # sections have to be adjacent as well as ordered. With a third between them the
    # whole group lands inside it, and the note then reports the claim and its
    # `[source]` line as outside the groups — blocks, twice, after the write.
    between = FLAT.replace(
        "## Relations", "## Open questions\n\n- what about latency\n\n## Relations"
    )

    assert codes(between) == ["sections_out_of_order"]


def test_a_tilde_in_the_knowledge_root_is_expanded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # `mise.toml` sets the documented default with a literal `~`. Unexpanded it
    # resolves under the working directory, so the check answers `no_knowledge_root`
    # at `base` — the phases skip and the notification blames a path that is right —
    # every night, on any install without a `.env` override.
    (tmp_path / "Knowledge" / "concepts").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KBOAT_KNOWLEDGE_PATH", raising=False)

    code, report = _run(["--knowledge", "~/Knowledge"], capsys)

    assert (code, report["findings"]) == (0, [])
    assert report["knowledge"] == str(tmp_path / "Knowledge")


def test_an_empty_knowledge_root_is_a_usage_error_not_the_working_directory(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # `Path("")` is `.`, so an empty variable would have the check report on the
    # working directory — a report about somewhere else, which is the one answer
    # this module must never give.
    monkeypatch.setenv("KBOAT_KNOWLEDGE_PATH", "")

    with pytest.raises(SystemExit) as exc:
        main([])

    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


def test_the_unclosed_count_is_the_number_of_runs_not_of_headings() -> None:
    # `kboat-curate` hands this `detail` to the human as the work item, so an
    # inflated count sends them hunting runs that do not exist in a note the pass is
    # already refusing.
    two_headings_one_run = FLAT.replace(
        "- [definition] a claim #grounded\n- [source] An Article — <https://example.com/a>",
        "### A\n\n- [definition] a claim #grounded\n\n### B\n\n### C",
    )

    (unclosed,) = [
        f for f in check_concept_note(two_headings_one_run, "n.md") if f.code == "unclosed_claims"
    ]
    assert unclosed.detail.startswith("1 run(s)")

    # And the other direction, which is the worse one: reported as one, a note with
    # two ungrouped runs sends the curator away after the first repair believing it
    # is done, while the pass keeps aborting every source needing the concept.
    two_runs = FLAT.replace(
        "- [definition] a claim #grounded\n- [source] An Article — <https://example.com/a>",
        "### A\n\n- [definition] one #grounded\n\n### B\n\n- [definition] two #grounded",
    )
    (two,) = [f for f in check_concept_note(two_runs, "n.md") if f.code == "unclosed_claims"]
    assert two.detail.startswith("2 run(s)")


def test_a_fence_indented_within_three_spaces_is_still_a_fence() -> None:
    # The boundary is pinned on the rejecting side only by the four-space case. A
    # fenced block kept inside a list item is indented by two, and `kboat-notes`
    # prescribes one for a multi-line formula — read as ordinary lines, a
    # `## Relations` inside it is counted twice or the real one is swallowed, both
    # `blocks`, on a note Basic Memory writes into happily.
    nested = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n\n  ```text\n  ## Relations\n  ```",
    )

    assert codes(nested) == []


def test_a_hash_run_with_no_delimiter_after_it_is_not_a_heading() -> None:
    # `###Heading` is a paragraph to markdown-it and to Basic Memory. Counted as a
    # group heading it shifts the group and heading counts apart, giving
    # `spare_headings` or `heading_group_mismatch` — blocks — on a note whose real
    # groups are all where they belong.
    typo = FLAT.replace(
        "- [definition] a claim #grounded",
        "###Heading\n\n- [definition] a claim #grounded",
    )

    assert codes(typo) == []


def test_a_deeply_indented_hash_run_is_not_a_heading() -> None:
    # Past three spaces it is a code block to markdown-it, not a heading. Counted as
    # a group heading it closes the run above it and shifts the counts apart —
    # `unclosed_claims` and `heading_group_mismatch`, both blocks, on a clean note.
    stub = GROUPED.replace(
        "- [definition] a claim #grounded",
        "- [definition] a claim #grounded\n\n    ### stub",
    )

    assert codes(stub) == []


def test_a_closing_fence_carrying_an_info_string_does_not_close() -> None:
    # Upstream requires the closing marker's suffix to be blank. Letting one close
    # re-admits the rest of the block, so a `[source]` line below it falls outside
    # the group and the note reports `unclosed_claims` — blocks — on a clean note.
    # The inner marker is *longer* than the opener, so only the blank-suffix rule
    # keeps it from closing; a shorter one is rejected on length first and never
    # reaches the condition under test.
    inner = FLAT.replace(
        "- [definition] a claim #grounded",
        "```text\n```` js\n- [insight] not a claim #grounded\n```\n\n- [definition] a claim #grounded",
    )

    assert codes(inner) == []


def test_a_section_abutting_relations_with_no_blank_line_is_still_between_them() -> None:
    # The adjacency test is an index comparison, so an off-by-one loses the case
    # where the intervening section sits directly above `## Relations` — and a group
    # inserted before that anchor then lands inside it.
    abutting = FLAT.replace("## Relations", "## Open questions\n## Relations")

    assert codes(abutting) == ["sections_out_of_order"]


def test_the_printed_report_carries_the_disposition_and_the_repair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Both readers are told to branch on the disposition and print the repair rather
    # than restate either, so the report is where they have to be. Every other CLI
    # case reads `note`, `code` and the counts, so the report could stop carrying
    # them and the suite would say nothing.
    root = _root(tmp_path, bad=FLAT.replace("## Relations", "## Later"))

    _, report = _run(["--knowledge", str(root)], capsys)

    (finding,) = report["findings"]
    assert finding["disposition"] == BLOCKS
    assert finding["repair"] == CODES[finding["code"]].repair


def test_a_closing_fence_longer_than_its_opener_does_close_it() -> None:
    # The rejecting side is pinned twice over; nothing said a longer marker closes.
    # Read as not closing, the fence swallows the rest of the file and both section
    # anchors go missing — blocks, on a note with nothing wrong.
    # Inside `## Observations`, so a fence that fails to close swallows the section
    # below it rather than trailing off the end of the file with nothing to take.
    text = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "```text\nx = 1\n````\n\n- [source] An Article — <https://example.com/a>",
    )

    assert codes(text) == []


def test_a_backtick_line_inside_a_tilde_fence_does_not_close_it() -> None:
    # A closing marker has to be the same character. Letting any marker close
    # re-admits the block's own lines, so a claim inside a formula becomes an
    # unclosed run.
    text = FLAT.replace(
        "- [definition] a claim #grounded",
        "~~~text\n```\n- [insight] not a claim #grounded\n~~~\n\n- [definition] a claim #grounded",
    )

    assert codes(text) == []


def test_a_fence_indented_three_spaces_is_still_a_fence() -> None:
    # Three is the upper bound, not two: the existing case uses two, so the bound
    # itself was free to move down a space.
    text = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n\n   ```text\n   ## Relations\n   ```",
    )

    assert codes(text) == []


def test_a_knowledge_root_reached_through_a_file_is_a_root_to_fix(tmp_path: Path) -> None:
    # `stat` answers `NotADirectoryError` when a path component is a file. Filed as
    # a refusal it reads "fix the access" — sending the human to change permissions
    # on a path that is simply wrong.
    (tmp_path / "afile").write_text("x", encoding="utf-8")

    (finding,) = _check_concepts(tmp_path / "afile" / "kboat")[1]

    assert finding.code == "no_knowledge_root"


def test_a_heading_standing_below_its_group_heads_nothing() -> None:
    # Counted as that group's own, it read `lone_group_headed` at `passes` — "leave
    # it, the next append settles it" — and the append then cemented a note whose
    # first group carried no heading at all, clean for good. That is the inference
    # from bullet position this change exists to remove.
    below = FLAT.replace("## Relations", "### A heading with nothing under it\n\n## Relations")

    assert codes(below) == ["spare_headings"]


def test_a_fence_outside_the_observations_block_is_not_scanned_for_claims() -> None:
    # `kboat-notes` puts no section bound on a fenced block, and markdown-it hands
    # one to the parser as fence content the graph reads nothing from. Scanned, a
    # changelog or YAML sample indexes and the note comes back
    # `claim_outside_observations` — blocks — nightly, against a clean note.
    sample = FLAT + "\n## Notes\n\n```yaml\n- name: worker  # pool\n```\n"

    assert codes(sample) == []


def test_the_first_of_two_consecutive_headings_heads_nothing() -> None:
    # The other side of the same rule: a heading immediately followed by another
    # heads nothing, and counting it makes the pair read as two groups' worth of
    # headings over the one group below.
    stacked = GROUPED.replace(
        "### What the second reading gave",
        "### An empty one\n\n### What the second reading gave",
    )

    assert codes(stacked) == ["spare_headings"]


def test_a_spare_heading_does_not_hide_groups_that_carry_none() -> None:
    # Reported through one `elif`, the spare suppressed the balance: the report
    # named the one heading to remove and nothing else, so a human who did exactly
    # what it asked was left with two unheaded groups — `heading_group_mismatch`,
    # blocks — and the note came back on the next run having been "repaired".
    ungrouped = (
        GROUPED.replace("### What the first reading gave\n\n", "")
        .replace("### What the second reading gave\n\n", "")
        .replace("## Relations", "### A heading with nothing under it\n\n## Relations")
    )

    assert codes(ungrouped) == ["spare_headings", "heading_group_mismatch"]


def test_the_spare_count_is_reported_apart_from_the_headed_groups() -> None:
    # Summed with the headings that do head a group, the detail read "3 heading(s)
    # over 2 reading group(s)" — which is what `heading_group_mismatch`'s own
    # wording calls a balanced note — and the repair's "leave at most one over the
    # group", read against a three-group note, deletes two correct headings.
    stranded = GROUPED.replace(
        "## Relations", "### A heading with nothing under it\n\n## Relations"
    )

    (finding,) = [f for f in check_concept_note(stranded, "n.md") if f.code == "spare_headings"]

    assert "1 heading(s) heading no reading group" in finding.detail
    assert "over 2 reading group(s)" in finding.detail


def test_a_relation_line_the_graph_indexes_has_no_exit_in_move_or_delete() -> None:
    # A `#`-word in an edge's context makes `is_observation` true of it, so the
    # check is right to fire — but the two exits a claim takes are both wrong here:
    # moving it lands the note on `unclosed_claims`, and taking it out drops a real
    # edge. The repair names rewording, which is why this pins both measurements.
    edge = FLAT.replace("- related_to [[Other]]", "- related_to [[Other]] (contrast with #ml)")
    moved = FLAT.replace(
        "- [source] An Article — <https://example.com/a>",
        "- [source] An Article — <https://example.com/a>\n- related_to [[Other]] (contrast with #ml)",
    )

    assert codes(edge) == ["claim_outside_observations"]
    assert codes(moved) == ["unclosed_claims"]
    assert "reword" in CODES["claim_outside_observations"].repair
