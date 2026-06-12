"""End-to-end tests for the `kboat-pick` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kboat_pick.__main__ import main
from kboat_pick.notes import parse_frontmatter


def _source(
    slug: str, *, source_type: str = "web_page", picked: bool = False, **flags: bool
) -> str:
    f = {"distill": False, "keep": False, "dismiss": False, "blocked": False, **flags}
    return (
        "---\n"
        "type: source\n"
        f"title: {slug} title\n"
        f"source_type: {source_type}\n"
        f"url: https://example.com/{slug}\n"
        f"reading_link: https://example.com/{slug}\n"
        f"summary: summary of {slug}.\n"
        "topics:\n"
        f"  - topic-{slug}\n"
        "added_date: 2026-06-01\n"
        "reading: false\n"
        f"distill: {str(f['distill']).lower()}\n"
        f"keep: {str(f['keep']).lower()}\n"
        f"dismiss: {str(f['dismiss']).lower()}\n"
        f"blocked: {str(f['blocked']).lower()}\n"
        f"picked: {str(picked).lower()}\n"
        f"notebooklm_id: id-{slug}\n"
        "---\n"
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    sources = tmp_path / "Sources"
    sources.mkdir()
    (sources / "web1.md").write_text(_source("web1"), encoding="utf-8")
    (sources / "web2.md").write_text(_source("web2"), encoding="utf-8")
    (sources / "kept.md").write_text(_source("kept", keep=True), encoding="utf-8")
    (sources / "doc.md").write_text(_source("doc", source_type="pdf"), encoding="utf-8")
    daily = tmp_path / "Daily"
    daily.mkdir()
    (daily / "2026-06-04.md").write_text("## 明日への問い\n- a question\n", encoding="utf-8")
    return tmp_path


def test_candidates_lists_only_active_web_plus_questions(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--vault", str(vault), "candidates", "--today", "2026-06-12"]) == 0
    out = json.loads(capsys.readouterr().out)
    slugs = {c["slug"] for c in out["candidates"]}
    assert slugs == {"web1", "web2"}  # kept (disposition) and doc (pdf) excluded
    assert out["counts"]["candidates_total"] == 2
    assert out["questions"] == [{"date": "2026-06-04", "items": ["a question"]}]
    assert out["candidates"][0]["topics"] == ["topic-web1"]


def test_set_marks_chosen_and_resets_rest(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--vault", str(vault), "set", "--slugs", "web1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["picked"] == ["web1"]
    assert out["reset"] == 3  # web2, kept, doc
    assert out["missing"] == []
    fm = lambda s: parse_frontmatter((vault / "Sources" / s).read_text(encoding="utf-8"))  # noqa: E731
    assert fm("web1.md")["picked"] is True
    assert fm("web2.md")["picked"] is False
    assert fm("kept.md")["picked"] is False


def test_set_reports_missing_slug(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--vault", str(vault), "set", "--slugs", "web1,ghost"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["picked"] == ["web1"]
    assert out["missing"] == ["ghost"]


def test_empty_slugs_clears_all(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--vault", str(vault), "set", "--slugs", "web1"])
    capsys.readouterr()
    assert main(["--vault", str(vault), "set", "--slugs", ""]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["picked"] == []
    assert out["reset"] == 4
    fm = parse_frontmatter((vault / "Sources" / "web1.md").read_text(encoding="utf-8"))
    assert fm["picked"] is False
