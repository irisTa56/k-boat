"""Tests for `kboat.knowledge`: which concept titles are flagged, and the tag census."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kboat.knowledge import (
    EvictedNotesError,
    UnreadableNotesError,
    flagged_titles,
    frontmatter,
    read_concepts,
    tag_census,
)

LONG_TITLE = (
    "Proximal policy optimization with generalized advantage estimation for large "
    "language model post-training"
)


def _write(root: Path, stem: str, fields: dict[str, object]) -> str:
    """Write a concept note as Basic Memory would, and return its frontmatter block."""
    concepts = root / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    # Basic Memory's own dump arguments (`dump_frontmatter` in `basic_memory.file_utils`),
    # so a long value folds here exactly as it does in the knowledge base.
    block = yaml.dump(
        fields,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        Dumper=yaml.SafeDumper,
    )
    (concepts / f"{stem}.md").write_text(f"---\n{block}---\n\nbody\n", encoding="utf-8")
    return block


def _write_raw(root: Path, stem: str, text: str) -> None:
    concepts = root / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / f"{stem}.md").write_text(text, encoding="utf-8")


@pytest.mark.parametrize("title", ["Clean note", "yes", "1.10", "Microsoft .NET", LONG_TITLE])
def test_a_title_that_is_its_filename_is_not_flagged(tmp_path: Path, title: str) -> None:
    _write(tmp_path, title, {"title": title, "type": "note"})
    assert flagged_titles(read_concepts(tmp_path)) == []


def test_the_long_title_really_is_folded(tmp_path: Path) -> None:
    # Guards the case above against going vacuous: without a continuation line it
    # would pass for a reader that only takes the first line.
    block = _write(tmp_path, LONG_TITLE, {"title": LONG_TITLE})
    assert "\n  " in block


def test_a_title_that_differs_from_its_filename_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "A - B pattern", {"title": "A / B pattern"})
    assert flagged_titles(read_concepts(tmp_path)) == [
        {"file": "concepts/A - B pattern.md", "title": "A / B pattern"}
    ]


@pytest.mark.parametrize("title", ["C#", "Block ^ref", "Array [T]"])
def test_a_title_carrying_wikilink_syntax_is_flagged(tmp_path: Path, title: str) -> None:
    _write(tmp_path, title, {"title": title})
    assert flagged_titles(read_concepts(tmp_path)) == [
        {"file": f"concepts/{title}.md", "title": title}
    ]


@pytest.mark.parametrize(
    "text",
    ["---\ntype: note\n---\n\nbody\n", "---\ntitle:\n  nested: mapping\n---\n"],
)
def test_a_missing_or_non_string_title_is_flagged_as_null(tmp_path: Path, text: str) -> None:
    _write_raw(tmp_path, "Some note", text)
    assert flagged_titles(read_concepts(tmp_path)) == [
        {"file": "concepts/Some note.md", "title": None}
    ]


@pytest.mark.parametrize(
    "text",
    [
        "---\ntitle: X\ntags:\n  - gpu\n - cuda\n---\n",
        "---\n- a list\n---\n\nbody\n",
        "no frontmatter at all\n",
    ],
)
def test_frontmatter_that_does_not_parse_refuses_the_whole_read(tmp_path: Path, text: str) -> None:
    # Read as empty, the first case would count as untagged, and the curate pass
    # would add a second `tags:` block to a note that already has one.
    _write(tmp_path, "Fine", {"title": "Fine", "tags": ["gpu"]})
    _write_raw(tmp_path, "Broken", text)
    _write_raw(tmp_path, "Also broken", "---\ntitle: [unclosed\n---\n")
    with pytest.raises(UnreadableNotesError) as exc:
        read_concepts(tmp_path)
    assert exc.value.files == ["concepts/Also broken.md", "concepts/Broken.md"]


def test_only_markdown_files_are_read(tmp_path: Path) -> None:
    _write(tmp_path, "Note", {"title": "Note"})
    (tmp_path / "concepts" / "image.png").write_bytes(b"\x89PNG")
    assert [note.file for note in read_concepts(tmp_path)] == ["concepts/Note.md"]


def test_an_evicted_concept_note_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "Present", {"title": "Present"})
    placeholder = tmp_path / "concepts" / ".Evicted.md.icloud"
    placeholder.write_bytes(b"")
    with pytest.raises(EvictedNotesError) as exc:
        read_concepts(tmp_path)
    assert exc.value.placeholders == [placeholder]


def test_a_stale_stub_beside_its_note_is_not_an_eviction(tmp_path: Path) -> None:
    _write(tmp_path, "Present", {"title": "Present"})
    (tmp_path / "concepts" / ".Present.md.icloud").write_bytes(b"")
    assert [note.stem for note in read_concepts(tmp_path)] == ["Present"]


def test_the_census_counts_block_and_flow_tags_most_used_first(tmp_path: Path) -> None:
    _write(tmp_path, "One", {"title": "One", "tags": ["gpu", "performance"]})
    _write(tmp_path, "Two", {"title": "Two", "tags": ["performance"]})
    _write_raw(tmp_path, "Three", "---\ntitle: Three\ntags: [performance, cuda]\n---\n")
    census = tag_census(read_concepts(tmp_path))
    assert census["counts"] == {"performance": 3, "cuda": 1, "gpu": 1}
    assert list(census["counts"]) == ["performance", "cuda", "gpu"]
    assert census["untagged"] == []


@pytest.mark.parametrize(
    "text",
    [
        "---\ntitle: X\n---\n",
        "---\ntitle: X\ntags:\n---\n",
        "---\ntitle: X\ntags: []\n---\n",
        "---\ntitle: X\ntags:\n  key: value\n---\n",
    ],
)
def test_a_note_with_no_tag_is_untagged(tmp_path: Path, text: str) -> None:
    _write_raw(tmp_path, "X", text)
    assert tag_census(read_concepts(tmp_path)) == {"counts": {}, "untagged": ["concepts/X.md"]}


def test_a_single_scalar_tag_is_counted(tmp_path: Path) -> None:
    _write_raw(tmp_path, "X", "---\ntitle: X\ntags: gpu\n---\n")
    assert tag_census(read_concepts(tmp_path)) == {"counts": {"gpu": 1}, "untagged": []}


def test_frontmatter_reads_scalars_as_the_strings_written() -> None:
    assert frontmatter("---\ndate: 2026-09-01\nflag: true\nempty:\n---\n") == {
        "date": "2026-09-01",
        "flag": "true",
        "empty": "",
    }
