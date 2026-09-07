"""Tests for `scripts/md_shape.py`.

Each case is a small markdown document written to a temp file, since `scan`
reads a path -- that is what the `qa:md` gate hands it. `conftest.py` puts
`scripts/` on `sys.path` so the import below resolves.

The pairs matter more than the singles: for every document that is reported
there is a near-identical one that is not, differing only by the indentation, the
marker, or the heading that decides it. A check that only ever sees faults cannot
show it is reading the rule rather than the shape.

Which fault a case yields is part of every assertion. The two overlap on most
documents -- a folded line is nearly always inside an item as well -- so a test
that only counted reports would not notice one branch answering for the other.
"""

from __future__ import annotations

from pathlib import Path

import md_shape
import pytest


def _scan(tmp_path: Path, text: str) -> list[md_shape.Report]:
    path = tmp_path / "doc.md"
    path.write_text(text, encoding="utf-8")
    return md_shape.scan(path)


def _found(reports: list[md_shape.Report]) -> list[tuple[str, int, int, int]]:
    """Each report as (kind, line, the line's column, the claiming item's)."""
    return [(r.kind, r.line_no, r.indent, r.item_column) for r in reports]


def test_line_under_a_deeper_child_is_folded(tmp_path: Path) -> None:
    reports = _scan(
        tmp_path,
        "- outer item\n  - deeper child\n  meant for the outer item\n",
    )
    assert _found(reports) == [(md_shape.FOLD, 3, 2, 4)]


def test_a_blank_line_before_it_leaves_prose_in_the_item(tmp_path: Path) -> None:
    # The same three lines, and the fix for the fold: the blank line ends the
    # child's paragraph, so the last line lands where its indentation puts it --
    # which is inside the outer item, and that is the second fault.
    reports = _scan(tmp_path, "- outer item\n  - deeper child\n\n  meant for the outer item\n")
    assert _found(reports) == [(md_shape.PROSE, 4, 2, 2)]


def test_prose_continuing_its_own_item_is_reported(tmp_path: Path) -> None:
    # At the item's own content column with no deeper child open: it renders
    # exactly where it reads, and a list item is still the line its marker is on.
    reports = _scan(tmp_path, "- outer item\n  continuing that same item\n")
    assert _found(reports) == [(md_shape.PROSE, 2, 2, 2)]


def test_a_line_indented_into_the_deeper_child_is_prose_not_a_fold(tmp_path: Path) -> None:
    reports = _scan(tmp_path, "- outer item\n  - deeper child\n    continuing the child\n")
    assert _found(reports) == [(md_shape.PROSE, 3, 4, 4)]


def test_an_unindented_line_after_a_nested_item_is_folded(tmp_path: Path) -> None:
    # Column 0 is still shallower than the open item, so the paragraph swallows
    # it just the same. No item's content column can reach column 0, so this is
    # the case only the fold sees.
    reports = _scan(tmp_path, "- outer item\n  - deeper child\nback at the margin\n")
    assert _found(reports) == [(md_shape.FOLD, 3, 0, 4)]


def test_a_heading_is_reported_as_neither(tmp_path: Path) -> None:
    # An ATX heading interrupts a paragraph rather than continuing it, and it is
    # what an item with prose under it is supposed to become.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n## A heading\n") == []


def test_a_blockquote_is_prose_in_the_item_not_a_fold(tmp_path: Path) -> None:
    # It interrupts the child's paragraph, so calling it folded would be false;
    # it still sits at the outer item's content column.
    reports = _scan(tmp_path, "- outer item\n  - deeper child\n  > quoted\n")
    assert _found(reports) == [(md_shape.PROSE, 3, 2, 2)]


def test_an_html_comment_is_prose_in_the_item_not_a_fold(tmp_path: Path) -> None:
    # Same again: pandoc puts this one in the outer item, where it reads -- and
    # inside an item is exactly what the second fault reports.
    reports = _scan(tmp_path, "- outer item\n  - deeper child\n  <!-- a note -->\n")
    assert _found(reports) == [(md_shape.PROSE, 3, 2, 2)]


def test_every_thematic_break_spelling_is_prose_in_the_item(tmp_path: Path) -> None:
    # CommonMark spells it three ways; reporting one as folded would name a
    # remedy -- a blank line -- that changes nothing about where it renders.
    for rule in ("---", "***", "___"):
        reports = _scan(tmp_path, f"- outer item\n  - deeper child\n  {rule}\n")
        assert _found(reports) == [(md_shape.PROSE, 3, 2, 2)], rule


def test_a_spaced_thematic_break_is_read_as_the_break_not_a_marker(tmp_path: Path) -> None:
    # `- - -` is both spellable as a list item and a thematic break; CommonMark
    # gives the break precedence, so it closes the child rather than opening an
    # item, and the next line is measured against the outer item alone.
    reports = _scan(tmp_path, "- outer item\n  - deeper child\n  - - -\n  after it\n")
    assert _found(reports) == [(md_shape.PROSE, 3, 2, 2), (md_shape.PROSE, 4, 2, 2)]


def test_an_indented_code_block_is_prose_in_the_item(tmp_path: Path) -> None:
    # Four columns past the enclosing item's content column with no paragraph
    # open is code, so the line after it continues nothing and is not folded.
    # Both lines are still blocks sitting inside the outer item.
    reports = _scan(
        tmp_path,
        "- outer item\n  - deeper child\n\n        indented code\n  back in the outer item\n",
    )
    assert _found(reports) == [(md_shape.PROSE, 4, 8, 4), (md_shape.PROSE, 5, 2, 2)]


def test_a_wide_marker_does_not_invent_a_content_column(tmp_path: Path) -> None:
    # More than four spaces after the marker: CommonMark puts the content column
    # at the marker's end plus one and reads the rest as code, so `next line` at
    # column 2 is inside the item rather than short of a column-6 one.
    reports = _scan(tmp_path, "-     wide marker\n  next line\n")
    assert _found(reports) == [(md_shape.PROSE, 2, 2, 2)]


def test_a_line_at_an_items_marker_column_is_folded(tmp_path: Path) -> None:
    # Short of the content column, so absent lazy continuation it would fall out
    # of the list entirely; laziness keeps it in the item. Confirmed with pandoc:
    # `  - second\n  y` renders `<li>second y</li>`, while a blank line between
    # them renders `y` as a paragraph outside the list.
    reports = _scan(tmp_path, "  - second\n  y\n")
    assert _found(reports) == [(md_shape.FOLD, 2, 2, 4)]


def test_a_heading_closes_the_items_it_outdents(tmp_path: Path) -> None:
    # Pins the heading branch's unwinding: without it the post-heading paragraph
    # is measured against a list the heading already ended.
    assert _scan(tmp_path, "- a\n## h\n  x\nx\n") == []


def test_a_heading_indented_inside_an_item_closes_it_too(tmp_path: Path) -> None:
    # The documented limit of the second fault: prose under a nested heading is
    # unreported, because a heading is taken to end the list wherever it sits.
    assert _scan(tmp_path, "- outer item\n  ## nested heading\n  prose under it\n") == []


def test_a_blank_separated_top_level_paragraph_closes_the_list(tmp_path: Path) -> None:
    # Pins the unwinding on the ordinary path, for the same reason.
    assert _scan(tmp_path, "- a\n\nx\nx\n") == []


def test_prose_beside_a_list_rather_than_in_it_is_not_reported(tmp_path: Path) -> None:
    # The shape the rule asks for: the list ends, and the prose stands alone.
    assert _scan(tmp_path, "- a\n- b\n\nA paragraph of its own.\n") == []


def test_fenced_code_is_not_scanned(tmp_path: Path) -> None:
    # Text inside a fence is content, not a paragraph line, and a `-` in it is
    # not a list marker.
    assert (
        _scan(
            tmp_path,
            "- outer item\n  - deeper child\n\n    ```text\n    - not a marker\n  short\n    ```\n",
        )
        == []
    )


def test_a_fenced_block_inside_an_item_is_not_reported(tmp_path: Path) -> None:
    # The other documented limit: a fence is skipped whatever it is nested in,
    # so a code block under an item goes unreported where a paragraph would not.
    assert _scan(tmp_path, "- outer item\n\n  ```text\n  code\n  ```\n") == []


def test_a_shorter_inner_fence_does_not_close_a_longer_one(tmp_path: Path) -> None:
    # How a skill file shows a fenced example: four backticks around three. The
    # inner block's contents are content, not markdown to scan.
    assert (
        _scan(
            tmp_path,
            "````markdown\n```text\n- outer item\n  - deeper child\n  stray line\n```\n````\n",
        )
        == []
    )


def test_a_fence_of_equal_length_does_close(tmp_path: Path) -> None:
    # The other half of the same rule: past the close, scanning resumes.
    reports = _scan(
        tmp_path,
        "```text\nnot scanned\n```\n\n- outer item\n  - deeper child\n  stray line\n",
    )
    assert _found(reports) == [(md_shape.FOLD, 7, 2, 4)]


def test_a_deeply_indented_line_continues_an_open_paragraph(tmp_path: Path) -> None:
    # An indented code block starts only where no paragraph is open. Treating
    # this one as code would close the child's paragraph and hide the fold on
    # the line after it, which pandoc puts inside that child.
    reports = _scan(
        tmp_path,
        "- outer item\n  - deeper child\n        deeply indented continuation\n  stray for outer\n",
    )
    assert _found(reports) == [(md_shape.PROSE, 3, 8, 4), (md_shape.FOLD, 4, 2, 4)]


def test_main_exits_two_on_a_file_that_is_not_utf8(tmp_path: Path, capsys) -> None:
    # A `ValueError`, so it escapes the `OSError` catch and would otherwise exit
    # 1 — the code reserved for "a fault is there".
    path = tmp_path / "latin1.md"
    path.write_bytes("- outer item\n  caf\xe9\n".encode("latin-1"))
    assert md_shape.main([str(path)]) == md_shape._EXIT_MALFORMED
    assert "not UTF-8 text" in capsys.readouterr().err


def test_frontmatter_dashes_are_not_list_markers(tmp_path: Path) -> None:
    # A skill file opens with `---`; without skipping it, the closing delimiter
    # would read as a list marker and every later line as its content.
    assert _scan(tmp_path, "---\nname: a-skill\n---\n\nPlain prose.\n") == []


def test_unterminated_frontmatter_is_scanned_from_the_top(tmp_path: Path) -> None:
    # No closing delimiter means no frontmatter, so the document is markdown
    # from line 1 and the list in it is measured like any other.
    reports = _scan(tmp_path, "---\n- outer item\n  continuing it\n")
    assert _found(reports) == [(md_shape.PROSE, 3, 2, 2)]


def test_ordered_markers_open_items_too(tmp_path: Path) -> None:
    reports = _scan(tmp_path, "1. outer step\n   1. deeper step\n   meant for the outer step\n")
    assert _found(reports) == [(md_shape.FOLD, 3, 3, 6)]


def test_several_folds_are_all_reported(tmp_path: Path) -> None:
    reports = _scan(
        tmp_path,
        "- outer item\n  - deeper child\n  first stray\n  second stray\n",
    )
    assert _found(reports) == [(md_shape.FOLD, 3, 2, 4), (md_shape.FOLD, 4, 2, 4)]


def test_several_prose_lines_are_all_reported(tmp_path: Path) -> None:
    reports = _scan(tmp_path, "- outer item\n\n  first paragraph\n\n  second paragraph\n")
    assert _found(reports) == [(md_shape.PROSE, 3, 2, 2), (md_shape.PROSE, 5, 2, 2)]


def test_main_reports_nothing_for_a_clean_file(tmp_path: Path, capsys) -> None:
    path = tmp_path / "clean.md"
    path.write_text("- outer item\n  - deeper child\n", encoding="utf-8")
    assert md_shape.main([str(path)]) == 0
    assert "no misshapen lines" in capsys.readouterr().out


def test_main_exits_one_and_names_a_folded_line(tmp_path: Path, capsys) -> None:
    path = tmp_path / "folded.md"
    path.write_text("- outer item\n  - deeper child\n  stray line\n", encoding="utf-8")
    assert md_shape.main([str(path)]) == md_shape._EXIT_FAULT
    out = capsys.readouterr().out
    assert f"{path}:3" in out
    assert "is folded into the item at column 4" in out
    assert "stray line" in out


def test_main_exits_one_and_names_a_line_of_prose_in_an_item(tmp_path: Path, capsys) -> None:
    # The second fault reaches the exit code too, and says its own remedy: a
    # blank line is not one, so the message must not offer it.
    path = tmp_path / "prose.md"
    path.write_text("- outer item\n\n  a paragraph under it\n", encoding="utf-8")
    assert md_shape.main([str(path)]) == md_shape._EXIT_FAULT
    out = capsys.readouterr().out
    assert f"{path}:3" in out
    assert "is prose inside the item at column 2" in out
    assert "a paragraph under it" in out


def test_main_exits_two_on_an_unreadable_path(tmp_path: Path, capsys) -> None:
    # Exit 2 is "the input isn't what this script expects", never exit 1.
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
