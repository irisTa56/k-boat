"""Tests for `scripts/md_shape.py`.

Each case is a small markdown document written to a temp file, since `scan`
reads a path -- that is what the `qa:md` gate hands it. `conftest.py` puts
`scripts/` on `sys.path` so the import below resolves.

The pairs matter more than the singles: for every document that is reported
there is a near-identical one that is not, differing only by the indentation,
the marker, or the heading that decides it. A check that only ever sees faults
cannot show it is reading the rule rather than the shape.

Half of these documents are here because a hand-written line classifier got
them wrong and blocked a correct commit -- a table under an item, an indented
code block that reads as a marker, the interior of an HTML comment. They are
kept as the parser's own regression suite: what they pin is not that this
script knows CommonMark, but that it never again answers CommonMark's question
itself.
"""

from __future__ import annotations

from pathlib import Path

import md_shape
import pytest


def _scan(tmp_path: Path, text: str) -> list[int]:
    """The lines reported in one document, which is the whole verdict now."""
    path = tmp_path / "doc.md"
    path.write_text(text, encoding="utf-8")
    return [report.line_no for report in md_shape.scan(path)]


def test_a_line_lazily_continued_into_a_deeper_item_is_reported(tmp_path: Path) -> None:
    # The author indented line 3 for the outer item; CommonMark's lazy
    # continuation appends it to the deeper child's open paragraph instead. It
    # is prose inside a list item either way, which is why the one rule covers
    # it and the message does not name the laziness.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n  meant for the outer item\n") == [3]


def test_a_blank_line_before_it_leaves_the_line_reported(tmp_path: Path) -> None:
    # The same three lines with the blank line that un-folds them: line 4 now
    # renders where its indentation puts it, inside the outer item, and is
    # still prose inside a list item. Pinning this is what says the fold and
    # the prose are one fault -- a message offering the blank line as a remedy
    # would be sending the author to a document this gate still rejects.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n\n  meant for the outer item\n") == [4]


def test_prose_continuing_its_own_item_is_reported(tmp_path: Path) -> None:
    # At the item's own content column with no deeper child open: it renders
    # exactly where it reads, and a list item is still the line its marker is
    # on. Only line 2 -- the marker's own line is the item, not prose in it.
    assert _scan(tmp_path, "- outer item\n  continuing that same item\n") == [2]


def test_a_line_indented_into_the_deeper_child_is_reported(tmp_path: Path) -> None:
    assert _scan(tmp_path, "- outer item\n  - deeper child\n    continuing the child\n") == [3]


def test_a_line_back_at_the_margin_after_a_nested_item_is_reported(tmp_path: Path) -> None:
    # Column 0 is shallower than any item's content column, so nothing but
    # lazy continuation can account for the line rendering inside the child.
    assert _scan(tmp_path, "- outer item\n  - deeper child\nback at the margin\n") == [3]


def test_a_line_at_an_items_marker_column_is_reported(tmp_path: Path) -> None:
    # Short of the content column, so absent lazy continuation it would fall
    # out of the list entirely. Confirmed with pandoc: `  - second\n  y`
    # renders `<li>second y</li>`.
    assert _scan(tmp_path, "  - second\n  y\n") == [2]


def test_every_line_of_one_paragraph_is_reported(tmp_path: Path) -> None:
    # The parser gives a paragraph a half-open line range; reporting it whole
    # is what says the arithmetic converting that range is right at both ends.
    assert _scan(tmp_path, "- outer item\n\n  first\n  second\n  third\n") == [3, 4, 5]


def test_several_paragraphs_in_one_item_are_all_reported(tmp_path: Path) -> None:
    assert _scan(tmp_path, "- outer item\n\n  first para\n\n  second para\n") == [3, 5]


def test_ordered_markers_open_items_too(tmp_path: Path) -> None:
    assert _scan(tmp_path, "1. outer step\n   1. deeper step\n   meant for the outer\n") == [3]


def test_an_ordered_list_starting_at_one_interrupts_a_paragraph(tmp_path: Path) -> None:
    # A paragraph above the list is no reason to stop reading the list under it.
    assert _scan(
        tmp_path,
        "A paragraph line.\n1. outer step\n   1. deeper step\n   meant for the outer\n",
    ) == [4]


def test_a_sibling_item_is_not_bound_by_the_interrupt_rules(tmp_path: Path) -> None:
    # `2.` outdents an item already open, so it joins that list rather than
    # starting one, and the rules for interrupting a paragraph do not reach it.
    # Reading it as the first item's prose would report line 3 as well.
    assert _scan(tmp_path, "1. step one\n   text under one\n2. step two\n   text under two\n") == [
        2,
        4,
    ]


def test_a_wide_marker_puts_code_on_the_marker_line_and_prose_after_it(tmp_path: Path) -> None:
    # More than four spaces after the marker: CommonMark reads the rest of line
    # 1 as an indented code block inside the item, so the item's own line holds
    # no paragraph at all and line 2 is the first prose in it.
    assert _scan(tmp_path, "-     wide marker\n  next line\n") == [2]


def test_an_empty_items_content_is_reported(tmp_path: Path) -> None:
    # An item with nothing on its line still claims what follows at its content
    # column -- pandoc renders `<li>x</li>` -- and none of that is the marker
    # line, so all of it is prose in the item.
    assert _scan(tmp_path, "- a\n-\n  x\n") == [3]


def test_an_item_leading_with_a_child_list_still_reports_prose_after_it(tmp_path: Path) -> None:
    # The outer item's own line carries no paragraph, so the first paragraph
    # inside it is not the marker line's and is reported.
    assert _scan(tmp_path, "-\n  - child\n\n  prose in the outer item\n") == [4]


def test_prose_under_a_heading_nested_in_an_item_is_reported(tmp_path: Path) -> None:
    # A heading indented inside an item does not end the list -- pandoc puts
    # the `<h2>` and the paragraph after it both inside the `<li>`. The line
    # classifier this replaces ended the list at any heading and missed this.
    assert _scan(tmp_path, "- outer item\n  ## nested heading\n  prose under it\n") == [3]


def test_a_line_outdenting_past_a_fence_is_prose_in_the_enclosing_item(tmp_path: Path) -> None:
    # Nothing continues a fenced block lazily, so line 6 closes the deeper
    # child and the fence with it and lands in the outer item as prose. Pandoc
    # renders `short` as the outer `<li>`'s text.
    text = "- outer item\n  - deeper child\n\n    ```text\n    - not a marker\n  short\n    ```\n"
    assert _scan(tmp_path, text) == [6]


def test_a_deeply_indented_continuation_and_the_line_after_it_are_reported(tmp_path: Path) -> None:
    # An indented code block starts only where no paragraph is open, so line 3
    # continues the child's paragraph rather than becoming code.
    text = "- outer item\n  - deeper child\n        deep continuation\n  stray for outer\n"
    assert _scan(tmp_path, text) == [3, 4]


def test_prose_after_a_blockquote_in_an_item_is_reported(tmp_path: Path) -> None:
    # The blockquote itself is exempt; what follows it in the item is not, and
    # the exemption must end where the quote does.
    assert _scan(tmp_path, "- outer item\n\n  > quoted\n\n  after it\n") == [5]


def test_a_list_after_a_blockquote_is_still_scanned(tmp_path: Path) -> None:
    # The other half: a blockquote that closes before the list must leave the
    # list scanned. Never leaving the quote would silence the whole document.
    assert _scan(tmp_path, "> quoted\n\n- outer item\n  prose in it\n") == [4]


def test_prose_after_an_html_block_in_an_item_is_reported(tmp_path: Path) -> None:
    assert _scan(tmp_path, "- outer item\n\n  <!-- a note -->\n\n  after it\n") == [5]


def test_prose_after_every_thematic_break_spelling_is_reported(tmp_path: Path) -> None:
    # CommonMark spells it three ways, and the break is a block rather than
    # prose, so only the paragraph after it is reported.
    for rule in ("---", "***", "___"):
        assert _scan(tmp_path, f"- outer item\n\n  {rule}\n\n  after it\n") == [5], rule


def test_a_spaced_thematic_break_is_read_as_the_break_not_a_marker(tmp_path: Path) -> None:
    # `- - -` is spellable as a list item and as a thematic break; CommonMark
    # gives the break precedence, so it closes the child and line 4 is prose in
    # the outer item rather than in a third-level one.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n  - - -\n  after it\n") == [4]


def test_a_gfm_table_under_an_item_is_not_reported(tmp_path: Path) -> None:
    # Seven files here use tables and GitHub renders GFM, so bare CommonMark is
    # the wrong parser: without the table extension every row of this document
    # is a paragraph line inside the item, and the gate blocks a correct commit.
    text = "- outer item\n\n  | a | b |\n  | - | - |\n  | 1 | 2 |\n"
    assert _scan(tmp_path, text) == []


def test_a_multi_line_html_comment_holding_a_bullet_is_not_reported(tmp_path: Path) -> None:
    # The block runs to the line holding `-->`, so its interior is not markdown
    # and there is no list in this document at all.
    text = "# T\n\n<!--\nTODO\n- a bullet in the comment\n-->\n\nOrdinary prose.\n"
    assert _scan(tmp_path, text) == []


def test_a_year_at_the_start_of_a_line_opens_no_list(tmp_path: Path) -> None:
    # An ordered list may interrupt a paragraph only where it starts at 1,
    # which keeps a sentence wrapped onto `2024.` one paragraph and outside any
    # list. Opening an item here reports a line pandoc never puts in one.
    text = (
        "Some prose ending with the year\n"
        "2024. That was the year it happened.\n"
        "And a third line of the same paragraph.\n"
    )
    assert _scan(tmp_path, text) == []


def test_a_bare_marker_after_a_nested_list_is_not_reported(tmp_path: Path) -> None:
    # A marker alone on its line is CommonMark's empty item, and this one
    # outdents the child rather than continuing its paragraph: pandoc renders
    # `<li></li>`, so reporting it would name a line that is a marker.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n-\n") == []


def test_an_empty_item_swallows_nothing_shallower_than_its_content(tmp_path: Path) -> None:
    # An empty item opens no paragraph, so nothing continues into it lazily:
    # pandoc puts `x` outside the list entirely.
    assert _scan(tmp_path, "- a\n-\n x\n") == []


def test_a_setext_underline_in_an_item_makes_a_heading_not_prose(tmp_path: Path) -> None:
    # `-` under an open paragraph underlines it rather than opening an item:
    # pandoc renders lines 1 to 3 as one `<h2>` inside the `<li>`. A heading is
    # what an item with prose under it is meant to become, so there is nothing
    # here to report -- and line 4, past the heading, is outside the list.
    assert _scan(tmp_path, "- outer item\n  text in the item\n  -\nback at the margin\n") == []


def test_an_indented_code_block_under_an_item_is_not_reported(tmp_path: Path) -> None:
    # A block that cannot live on the marker's line, so the remedy the rule
    # names is not open to it -- and the fenced spelling of the same block is
    # not reported either, which is what keeps the verdict off the spelling.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n\n        indented code\n") == []


def test_an_indented_code_block_reading_as_a_marker_is_not_reported(tmp_path: Path) -> None:
    # Four columns past the item's content column: code, whatever its first
    # line looks like.
    assert _scan(tmp_path, "- outer item\n\n      - not a marker, this is code\n") == []


def test_a_fenced_block_inside_an_item_is_not_reported(tmp_path: Path) -> None:
    assert _scan(tmp_path, "- outer item\n\n  ```text\n  code\n  ```\n") == []


def test_a_shorter_inner_fence_does_not_close_a_longer_one(tmp_path: Path) -> None:
    # How a skill file shows a fenced example: four backticks around three.
    text = "````markdown\n```text\n- outer item\n  - deeper child\n  stray line\n```\n````\n"
    assert _scan(tmp_path, text) == []


def test_scanning_resumes_past_a_closing_fence(tmp_path: Path) -> None:
    text = "```text\nnot scanned\n```\n\n- outer item\n  - deeper child\n  stray line\n"
    assert _scan(tmp_path, text) == [7]


def test_an_indented_paragraph_after_a_column_zero_fence_is_not_reported(tmp_path: Path) -> None:
    # The fence at the margin closes the list, so the two-space indent on line
    # 7 belongs to no item -- it is a paragraph of its own, and an indent short
    # of four columns is not code either.
    text = "- outer item\n\n```text\ncode\n```\n\n  an indented paragraph\n"
    assert _scan(tmp_path, text) == []


def test_a_blockquote_under_an_item_is_not_reported(tmp_path: Path) -> None:
    # Its paragraphs are the quoted author's, and the rule is about this
    # repository's own prose.
    assert _scan(tmp_path, "- outer item\n\n  > quoted prose\n  > more quoted prose\n") == []


def test_a_paragraph_beside_a_list_rather_than_in_it_is_not_reported(tmp_path: Path) -> None:
    # The shape the rule asks for: the list ends, and the prose stands alone.
    assert _scan(tmp_path, "- a\n- b\n\nA paragraph of its own.\n") == []


def test_a_heading_at_the_margin_ends_the_list(tmp_path: Path) -> None:
    assert _scan(tmp_path, "- a\n## h\n\nx\nx\n") == []


def test_a_thematic_break_at_the_margin_ends_the_list(tmp_path: Path) -> None:
    # Pandoc puts the paragraph after the `<hr />`, outside the `<ul>`.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n***\n\n  after it\n") == []


def test_frontmatter_is_not_scanned_as_markdown(tmp_path: Path) -> None:
    # A block scalar's value can be shaped exactly like a list with a line
    # folded into it, and scanning it would report line 4.
    text = "---\ndescription: |\n  - a bullet in the value\n  a lazy line\n---\n"
    assert _scan(tmp_path, text) == []


def test_frontmatter_is_blanked_rather_than_dropped(tmp_path: Path) -> None:
    # Every reported line number has to be the file's own, so the skipped lines
    # stay in the document as blanks. Dropping them would name line 2 here.
    assert _scan(tmp_path, "---\ntitle: t\n---\n\n- outer item\n  continuing it\n") == [6]


def test_unterminated_frontmatter_is_scanned_from_the_top(tmp_path: Path) -> None:
    # No closing delimiter means no frontmatter, so the document is markdown
    # from line 1 and the list in it is measured like any other.
    assert _scan(tmp_path, "---\n- outer item\n  continuing it\n") == [3]


def test_a_document_with_no_list_at_all_is_not_reported(tmp_path: Path) -> None:
    assert _scan(tmp_path, "# T\n\nOne paragraph.\n\nAnd a second.\n") == []


def test_the_two_exit_codes_are_the_ones_the_gate_reads() -> None:
    # Every other test here names them by constant, so their values are pinned
    # once: 1 is "a fault is there" and 2 is "the input isn't what this script
    # expects", the same split `coverage_floor.py` draws.
    assert (md_shape._EXIT_FAULT, md_shape._EXIT_MALFORMED) == (1, 2)


def test_main_reports_nothing_for_a_clean_file(tmp_path: Path, capsys) -> None:
    path = tmp_path / "clean.md"
    path.write_text("- outer item\n  - deeper child\n", encoding="utf-8")
    assert md_shape.main([str(path)]) == 0
    assert "no misshapen lines" in capsys.readouterr().out


def test_main_exits_one_and_names_the_line_and_its_remedy(tmp_path: Path, capsys) -> None:
    path = tmp_path / "prose.md"
    path.write_text("- outer item\n\n  a paragraph under it\n", encoding="utf-8")
    assert md_shape.main([str(path)]) == md_shape._EXIT_FAULT
    out = capsys.readouterr().out
    assert f"{path}:3" in out
    assert "is prose inside a list item" in out
    assert "give the item a heading of its own" in out
    assert "a paragraph under it" in out


def test_main_exits_two_on_a_file_that_is_not_utf8(tmp_path: Path, capsys) -> None:
    # A `ValueError`, so it escapes the `OSError` catch and would otherwise
    # exit 1 -- the code reserved for "a fault is there".
    path = tmp_path / "latin1.md"
    path.write_bytes("- outer item\n  caf\xe9\n".encode("latin-1"))
    assert md_shape.main([str(path)]) == md_shape._EXIT_MALFORMED
    assert "not UTF-8 text" in capsys.readouterr().err


def test_main_exits_two_on_an_unreadable_path(tmp_path: Path, capsys) -> None:
    assert md_shape.main([str(tmp_path / "absent.md")]) == md_shape._EXIT_MALFORMED
    assert "cannot read" in capsys.readouterr().err


def test_main_with_no_paths_scans_the_repository(monkeypatch, tmp_path: Path, capsys) -> None:
    # The invocation `qa:md` and CI both use. A pathspec that silently stopped
    # matching would otherwise leave the gate green while scanning nothing.
    clean = tmp_path / "clean.md"
    clean.write_text("- outer item\n  - deeper child\n", encoding="utf-8")
    monkeypatch.setattr(md_shape, "tracked_markdown", lambda: [clean])
    assert md_shape.main([]) == 0
    assert "1 file(s)" in capsys.readouterr().out


def test_main_exits_two_when_the_repository_tracks_no_markdown(monkeypatch, capsys) -> None:
    monkeypatch.setattr(md_shape, "tracked_markdown", list)
    assert md_shape.main([]) == md_shape._EXIT_MALFORMED
    assert "no markdown files" in capsys.readouterr().err


def test_tracked_markdown_finds_this_repositorys_own_files() -> None:
    # Exercises the `git ls-files` call itself, which the two tests above stub.
    paths = md_shape.tracked_markdown()
    assert paths
    assert all(path.suffix in {".md", ".markdown"} for path in paths)
    assert Path("CLAUDE.md") in paths


def test_scan_rejects_a_directory(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        md_shape.scan(tmp_path)
