"""Tests for open-questions backlog extraction."""

from __future__ import annotations

from pathlib import Path

from kboat.pick.questions import extract_questions


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_top_level_bullets_become_ranked_questions(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "Questions.md",
        "- highest interest\n- middle interest\n- lowest interest\n",
    )
    out = extract_questions(f)
    assert [q.rank for q in out] == [1, 2, 3]
    assert [q.question for q in out] == ["highest interest", "middle interest", "lowest interest"]
    assert all(q.note == "" for q in out)


def test_leading_blank_line_and_marker_variants(tmp_path: Path) -> None:
    # The real file opens with a blank line; `*` and `+` are also valid markers.
    f = _write(tmp_path / "Questions.md", "\n- dash item\n* star item\n+ plus item\n")
    out = extract_questions(f)
    assert [q.question for q in out] == ["dash item", "star item", "plus item"]


def test_nested_sub_bullet_is_the_note(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "Questions.md",
        "- a question\n    - some context for it\n- another question\n",
    )
    out = extract_questions(f)
    assert out[0].question == "a question"
    assert out[0].note == "some context for it"
    assert out[1].question == "another question"
    assert out[1].note == ""


def test_multiple_note_lines_joined(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "Questions.md",
        "- q\n    - first note line\n    - second note line\n      continuation prose\n",
    )
    out = extract_questions(f)
    assert out[0].note == "first note line\nsecond note line\ncontinuation prose"


def test_heading_and_stray_prose_ignored(tmp_path: Path) -> None:
    # A non-indented non-bullet line (heading or paragraph) carries no question and
    # closes the current one, so trailing indented text does not attach to it.
    f = _write(
        tmp_path / "Questions.md",
        "# Questions\n\n- real question\n\nsome stray paragraph\n    orphaned indent\n",
    )
    out = extract_questions(f)
    assert [q.question for q in out] == ["real question"]
    assert out[0].note == ""


def test_frontmatter_is_stripped(tmp_path: Path) -> None:
    f = _write(tmp_path / "Questions.md", "---\ntags: [meta]\n---\n\n- q after frontmatter\n")
    out = extract_questions(f)
    assert [q.question for q in out] == ["q after frontmatter"]


def test_bare_marker_and_whitespace_are_not_questions(tmp_path: Path) -> None:
    # A marker with no text is noise, not an empty question.
    f = _write(tmp_path / "Questions.md", "-\n-   \n- real\n")
    out = extract_questions(f)
    assert [q.question for q in out] == ["real"]
    assert out[0].rank == 1


def test_note_before_any_question_is_dropped(tmp_path: Path) -> None:
    # Indented text with no open question attaches to nothing.
    f = _write(tmp_path / "Questions.md", "    - orphan note\n- first question\n")
    out = extract_questions(f)
    assert [q.question for q in out] == ["first question"]
    assert out[0].note == ""


def test_crlf_line_endings_are_parsed(tmp_path: Path) -> None:
    # A file edited on another platform uses CRLF; parsing must yield clean
    # questions and notes with no stray `\r` left on either.
    f = tmp_path / "Questions.md"
    f.write_bytes(b"- first question\r\n    - its note\r\n- second question\r\n")
    out = extract_questions(f)
    assert [q.question for q in out] == ["first question", "second question"]
    assert out[0].note == "its note"


def test_empty_file_is_empty_backlog(tmp_path: Path) -> None:
    assert extract_questions(_write(tmp_path / "Questions.md", "\n\n")) == []


def test_missing_file_is_empty_backlog(tmp_path: Path) -> None:
    assert extract_questions(tmp_path / "Questions.md") == []
