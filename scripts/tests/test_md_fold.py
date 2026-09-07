"""Tests for `scripts/md_fold.py`.

Each case is a small markdown document written to a temp file, since `scan`
reads a path -- that is what the `qa:md` gate hands it. `conftest.py` puts
`scripts/` on `sys.path` so the import below resolves.

The pairs matter more than the singles: for every document that folds there is
a near-identical one that does not, differing only by the blank line or the
indentation that decides it. A check that only ever sees folds cannot show it
is reading the rule rather than the shape.
"""

from __future__ import annotations

from pathlib import Path

import md_fold
import pytest


def _scan(tmp_path: Path, text: str) -> list[md_fold.Fold]:
    path = tmp_path / "doc.md"
    path.write_text(text, encoding="utf-8")
    return md_fold.scan(path)


def test_line_under_a_deeper_child_is_folded(tmp_path: Path) -> None:
    folds = _scan(
        tmp_path,
        "- outer item\n  - deeper child\n  meant for the outer item\n",
    )
    assert len(folds) == 1
    assert folds[0].line_no == 3
    assert (folds[0].indent, folds[0].swallowed_by) == (2, 4)


def test_a_blank_line_before_it_closes_the_paragraph(tmp_path: Path) -> None:
    # The same three lines, and the fix: the blank line ends the child's
    # paragraph, so the last line lands where its indentation puts it.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n\n  meant for the outer item\n") == []


def test_prose_continuing_its_own_item_is_not_folded(tmp_path: Path) -> None:
    # At the item's own content column with no deeper child open: this renders
    # exactly where it reads, whatever the writing conventions say about it.
    assert _scan(tmp_path, "- outer item\n  continuing that same item\n") == []


def test_a_line_indented_into_the_deeper_child_is_not_folded(tmp_path: Path) -> None:
    assert _scan(tmp_path, "- outer item\n  - deeper child\n    continuing the child\n") == []


def test_an_unindented_line_after_a_nested_item_is_folded(tmp_path: Path) -> None:
    # Column 0 is still shallower than the open item, so the paragraph swallows
    # it just the same.
    folds = _scan(tmp_path, "- outer item\n  - deeper child\nback at the margin\n")
    assert len(folds) == 1
    assert (folds[0].indent, folds[0].swallowed_by) == (0, 4)


def test_a_heading_is_not_folded(tmp_path: Path) -> None:
    # An ATX heading interrupts a paragraph rather than continuing it.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n## A heading\n") == []


def test_a_blockquote_is_not_folded(tmp_path: Path) -> None:
    # Interrupts a paragraph too, so flagging it would block a correct commit.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n  > quoted\n") == []


def test_a_thematic_break_is_not_folded(tmp_path: Path) -> None:
    assert _scan(tmp_path, "- outer item\n  - deeper child\n***\n") == []


def test_an_html_comment_is_not_folded(tmp_path: Path) -> None:
    # An HTML block interrupts a paragraph, and a comment is the kind that turns
    # up in prose. Pandoc puts this one in the outer item, where it reads.
    assert _scan(tmp_path, "- outer item\n  - deeper child\n  <!-- a note -->\n") == []


def test_an_indented_code_block_opens_no_paragraph(tmp_path: Path) -> None:
    # Four columns past the enclosing item's content column with no paragraph
    # open is code, so the line after it continues nothing.
    assert (
        _scan(
            tmp_path,
            "- outer item\n  - deeper child\n\n        indented code\n  back in the outer item\n",
        )
        == []
    )


def test_a_wide_marker_does_not_invent_a_content_column(tmp_path: Path) -> None:
    # More than four spaces after the marker: CommonMark puts the content column
    # at the marker's end plus one and reads the rest as code, so `next line` at
    # column 2 is inside the item rather than short of a column-6 one.
    assert _scan(tmp_path, "-     wide marker\n  next line\n") == []


def test_a_line_at_an_items_marker_column_is_reported(tmp_path: Path) -> None:
    # Short of the content column, so absent lazy continuation it would fall out
    # of the list entirely; laziness keeps it in the item. Confirmed with pandoc:
    # `  - second\n  y` renders `<li>second y</li>`, while a blank line between
    # them renders `y` as a paragraph outside the list.
    folds = _scan(tmp_path, "  - second\n  y\n")
    assert len(folds) == 1
    assert (folds[0].indent, folds[0].swallowed_by) == (2, 4)


def test_a_heading_closes_the_items_it_outdents(tmp_path: Path) -> None:
    # Pins the heading branch's pop: without it the post-heading paragraph is
    # measured against a list the heading already ended.
    assert _scan(tmp_path, "- a\n## h\n  x\nx\n") == []


def test_a_blank_separated_top_level_paragraph_closes_the_list(tmp_path: Path) -> None:
    # Pins the fallthrough's pop, for the same reason.
    assert _scan(tmp_path, "- a\n\nx\nx\n") == []


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


def test_frontmatter_dashes_are_not_list_markers(tmp_path: Path) -> None:
    # A skill file opens with `---`; without skipping it, the closing delimiter
    # would read as a list marker and every later line as its content.
    assert _scan(tmp_path, "---\nname: a-skill\n---\n\nPlain prose.\n") == []


def test_ordered_markers_open_items_too(tmp_path: Path) -> None:
    folds = _scan(tmp_path, "1. outer step\n   1. deeper step\n   meant for the outer step\n")
    assert len(folds) == 1
    assert (folds[0].indent, folds[0].swallowed_by) == (3, 6)


def test_several_folds_are_all_reported(tmp_path: Path) -> None:
    folds = _scan(
        tmp_path,
        "- outer item\n  - deeper child\n  first stray\n  second stray\n",
    )
    assert [fold.line_no for fold in folds] == [3, 4]


def test_main_reports_nothing_for_a_clean_file(tmp_path: Path, capsys) -> None:
    path = tmp_path / "clean.md"
    path.write_text("- outer item\n  - deeper child\n", encoding="utf-8")
    assert md_fold.main([str(path)]) == 0
    assert "no folded lines" in capsys.readouterr().out


def test_main_exits_one_and_names_the_line(tmp_path: Path, capsys) -> None:
    path = tmp_path / "folded.md"
    path.write_text("- outer item\n  - deeper child\n  stray line\n", encoding="utf-8")
    assert md_fold.main([str(path)]) == md_fold._EXIT_FOLD
    out = capsys.readouterr().out
    assert f"{path}:3" in out
    assert "stray line" in out


def test_main_exits_two_on_an_unreadable_path(tmp_path: Path, capsys) -> None:
    # Exit 2 is "the input isn't what this script expects", never exit 1.
    assert md_fold.main([str(tmp_path / "absent.md")]) == md_fold._EXIT_MALFORMED
    assert "cannot read" in capsys.readouterr().err


def test_main_with_no_paths_scans_the_repository(monkeypatch, tmp_path: Path, capsys) -> None:
    # The invocation `qa:md` and CI both use. A pathspec that silently stopped
    # matching would otherwise leave the gate green while scanning nothing.
    clean = tmp_path / "clean.md"
    clean.write_text("- outer item\n  - deeper child\n", encoding="utf-8")
    monkeypatch.setattr(md_fold, "tracked_markdown", lambda: [clean])
    assert md_fold.main([]) == 0
    assert "1 file(s)" in capsys.readouterr().out


def test_main_exits_two_when_the_repository_tracks_no_markdown(monkeypatch, capsys) -> None:
    monkeypatch.setattr(md_fold, "tracked_markdown", list)
    assert md_fold.main([]) == md_fold._EXIT_MALFORMED
    assert "no markdown files" in capsys.readouterr().err


def test_tracked_markdown_finds_this_repositorys_own_files() -> None:
    # Exercises the `git ls-files` call itself, which the two tests above stub.
    paths = md_fold.tracked_markdown()
    assert paths
    assert all(path.suffix in {".md", ".markdown"} for path in paths)
    assert Path("CLAUDE.md") in paths


def test_scan_rejects_a_directory(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        md_fold.scan(tmp_path)
