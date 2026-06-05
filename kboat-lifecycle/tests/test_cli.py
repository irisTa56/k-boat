"""End-to-end CLI tests over a temporary vault."""

import json
from pathlib import Path

import pytest

from kboat_lifecycle.__main__ import main

NOTE_TEMPLATE = """\
---
type: source
title: {title}
read: false
distill: {distill}
keep: {keep}
dismiss: {dismiss}
source_type: web_page
url: https://example.com/{slug}
filed_date:{filed_suffix}
distilled_date:
blocked: {blocked}
notebooklm_id: nb-{slug}
---
"""


def write_note(
    sources: Path,
    slug: str,
    *,
    distill=False,
    keep=False,
    dismiss=False,
    blocked=False,
    filed_date=None,
):
    (sources / f"{slug}.md").write_text(
        NOTE_TEMPLATE.format(
            title=slug,
            slug=slug,
            distill=str(distill).lower(),
            keep=str(keep).lower(),
            dismiss=str(dismiss).lower(),
            blocked=str(blocked).lower(),
            filed_suffix=f" {filed_date}" if filed_date else "",
        ),
        encoding="utf-8",
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Sources").mkdir()
    return tmp_path


def run(vault: Path, capsys, *extra: str) -> dict:
    rc = main(["--vault", str(vault), "--today", "2026-06-15", *extra])
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def test_stamps_filed_date_on_disk(vault: Path, capsys):
    sources = vault / "Sources"
    write_note(sources, "a", distill=True)
    out = run(vault, capsys)

    assert [s["slug"] for s in out["phase_a"]["stamped"]] == ["a"]
    assert "filed_date: 2026-06-15" in (sources / "a.md").read_text()


def test_clears_filed_date_on_disk(vault: Path, capsys):
    sources = vault / "Sources"
    # Filed but every disposition unchecked → Phase A re-arms it by clearing.
    write_note(sources, "a", filed_date="2026-06-01")
    out = run(vault, capsys)

    assert [s["slug"] for s in out["phase_a"]["cleared"]] == ["a"]
    assert "filed_date:\n" in (sources / "a.md").read_text()


def test_dry_run_does_not_write(vault: Path, capsys):
    sources = vault / "Sources"
    write_note(sources, "a", distill=True)
    out = run(vault, capsys, "--dry-run")

    assert out["dry_run"] is True
    assert [s["slug"] for s in out["phase_a"]["stamped"]] == ["a"]
    # The file still has an empty filed_date.
    assert "filed_date:\n" in (sources / "a.md").read_text()


def test_ripe_and_dismiss_work_sets(vault: Path, capsys):
    sources = vault / "Sources"
    write_note(sources, "ripe", distill=True, filed_date="2026-06-01")
    write_note(sources, "drop", dismiss=True, filed_date="2026-06-01")
    write_note(sources, "shelf", keep=True, filed_date="2026-06-01")
    out = run(vault, capsys)

    assert [s["slug"] for s in out["phase_b"]["ripe"]] == ["ripe"]
    assert [s["slug"] for s in out["phase_b"]["dismiss_discard"]] == ["drop"]
    assert out["counts"]["keep_noop"] == 1


def test_blocked_excluded_from_everything(vault: Path, capsys):
    sources = vault / "Sources"
    write_note(sources, "b", distill=True, blocked=True, filed_date="2026-06-01")
    out = run(vault, capsys)

    assert out["phase_a"]["stamped"] == []
    assert out["phase_b"]["ripe"] == []
    assert out["counts"]["blocked_excluded"] == 1


def test_non_source_note_is_an_anomaly(vault: Path, capsys):
    (vault / "Sources" / "weird.md").write_text(
        "---\ntype: review\nfiled_date:\n---\n", encoding="utf-8"
    )
    out = run(vault, capsys)
    assert len(out["anomalies"]) == 1
    assert out["anomalies"][0]["path"] == "Sources/weird.md"


def test_missing_vault_errors(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        main(["--vault", str(tmp_path / "nope"), "--today", "2026-06-15"])
    assert exc.value.code != 0
