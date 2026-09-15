"""End-to-end tests for the `kboat-knowledge` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kboat.knowledge.__main__ import main


def _note(root: Path, stem: str, text: str) -> None:
    concepts = root / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / f"{stem}.md").write_text(text, encoding="utf-8")


def test_titles_prints_the_flagged_notes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Keys asserted as the literals the record publishes: kboat-curate reads them.
    _note(tmp_path, "Clean", "---\ntitle: Clean\n---\n")
    _note(tmp_path, "A - B", "---\ntitle: A / B\n---\n")
    assert main(["--knowledge", str(tmp_path), "titles"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "flagged": [{"file": "concepts/A - B.md", "title": "A / B"}]
    }


def test_tags_prints_the_census(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _note(tmp_path, "One", "---\ntitle: One\ntags:\n- gpu\n---\n")
    _note(tmp_path, "Two", "---\ntitle: Two\n---\n")
    assert main(["--knowledge", str(tmp_path), "tags"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "counts": {"gpu": 1},
        "untagged": ["concepts/Two.md"],
    }


def test_the_root_defaults_to_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _note(tmp_path, "Clean", "---\ntitle: Clean\n---\n")
    monkeypatch.setenv("KBOAT_KNOWLEDGE_PATH", str(tmp_path))
    assert main(["titles"]) == 0
    assert json.loads(capsys.readouterr().out) == {"flagged": []}


def test_no_root_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KBOAT_KNOWLEDGE_PATH", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["titles"])
    assert exc.value.code == 2


def test_a_root_without_concepts_is_a_usage_error(tmp_path: Path) -> None:
    # An empty answer here would read as a base with nothing in it.
    with pytest.raises(SystemExit) as exc:
        main(["--knowledge", str(tmp_path), "tags"])
    assert exc.value.code == 2


def test_an_evicted_note_fails_with_nothing_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _note(tmp_path, "Present", "---\ntitle: Present\n---\n")
    (tmp_path / "concepts" / ".Evicted.md.icloud").write_bytes(b"")
    assert main(["--knowledge", str(tmp_path), "tags"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert ".Evicted.md.icloud" in captured.err


def test_a_note_whose_frontmatter_does_not_parse_fails_with_nothing_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _note(tmp_path, "Fine", "---\ntitle: Fine\n---\n")
    _note(tmp_path, "Broken", "---\ntitle: Broken\ntags:\n  - gpu\n - cuda\n---\n")
    assert main(["--knowledge", str(tmp_path), "tags"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "concepts/Broken.md" in captured.err


@pytest.mark.parametrize(
    "error",
    [PermissionError(13, "Permission denied"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")],
)
def test_an_unreadable_base_fails_with_nothing_on_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    (tmp_path / "concepts").mkdir()

    def refuse(root: Path) -> list[object]:
        raise error

    monkeypatch.setattr("kboat.knowledge.__main__.read_concepts", refuse)
    assert main(["--knowledge", str(tmp_path), "titles"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not read the knowledge base" in captured.err
