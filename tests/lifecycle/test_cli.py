"""End-to-end CLI tests over a temporary vault."""

import json
from pathlib import Path

import pytest

from kboat.lifecycle.__main__ import main

NOTE_TEMPLATE = """\
---
type: source
title: {title}
reading: false
distill: {distill}
keep: {keep}
dismiss: {dismiss}
source_type: web_page
url: https://example.com/{slug}
summary: {summary}
{topics_field}
filed_date:{filed_suffix}
distilled_date:
blocked: {blocked}
notebooklm_id:{notebook_suffix}
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
    summary="a summary",
    topics=("t",),  # source notes carry block-style topics; () means empty
    notebook=True,
):
    # Real source notes write topics as a YAML block list (the reader returns an
    # inline flow list as a plain string, so block is what "a populated list"
    # must look like). An empty tuple renders the bare `topics:` (parsed None).
    topics_field = "topics:\n" + "".join(f"  - {t}\n" for t in topics) if topics else "topics:"
    (sources / f"{slug}.md").write_text(
        NOTE_TEMPLATE.format(
            title=slug,
            slug=slug,
            distill=str(distill).lower(),
            keep=str(keep).lower(),
            dismiss=str(dismiss).lower(),
            blocked=str(blocked).lower(),
            summary=summary,
            topics_field=topics_field.rstrip("\n"),
            filed_suffix=f" {filed_date}" if filed_date else "",
            notebook_suffix=f" nb-{slug}" if notebook else "",
        ),
        encoding="utf-8",
    )


KINDLE_TEMPLATE = """\
---
type: kindle
title: {title}
reading_link: https://read.amazon.co.jp/?asin={slug}
author:
  - Someone
store_link: https://www.amazon.co.jp/dp/{slug}
distill: {distill}
distilled_date:{distilled_suffix}
added_date: 2026-06-01
---

{body}
"""


def write_kindle(kindles: Path, slug: str, *, distill=False, distilled_date=None, body="highlight"):
    (kindles / f"{slug}.md").write_text(
        KINDLE_TEMPLATE.format(
            title=slug,
            slug=slug,
            distill=str(distill).lower(),
            distilled_suffix=f" {distilled_date}" if distilled_date else "",
            body=body,
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


def test_needs_summary_surfaced_and_filtered(vault: Path, capsys):
    sources = vault / "Sources"
    write_note(sources, "gap", summary="", topics=())  # empty guide, live notebook
    write_note(sources, "full")  # populated — excluded
    write_note(sources, "nonb", summary="", topics=(), notebook=False)  # no notebook
    write_note(sources, "walled", summary="", topics=(), blocked=True)  # DLQ
    out = run(vault, capsys)

    assert [s["slug"] for s in out["needs_summary"]] == ["gap"]
    assert out["counts"]["needs_summary"] == 1


def test_non_source_note_is_an_anomaly(vault: Path, capsys):
    (vault / "Sources" / "weird.md").write_text(
        "---\ntype: review\nfiled_date:\n---\n", encoding="utf-8"
    )
    out = run(vault, capsys)
    assert len(out["anomalies"]) == 1
    assert out["anomalies"][0]["path"] == "Sources/weird.md"


def test_missing_kindles_dir_is_empty(vault: Path, capsys):
    # Kindles/ is optional — a sources-only vault must not error.
    out = run(vault, capsys)
    assert out["kindles"]["ripe"] == []
    assert out["counts"]["kindles_total"] == 0


def test_kindle_ripe_selection(vault: Path, capsys):
    kindles = vault / "Kindles"
    kindles.mkdir()
    write_kindle(kindles, "B001RIPE", distill=True)
    write_kindle(kindles, "B002IDLE")  # distill unchecked
    write_kindle(kindles, "B003DONE", distill=True, distilled_date="2026-06-10")
    out = run(vault, capsys)

    assert [k["slug"] for k in out["kindles"]["ripe"]] == ["B001RIPE"]
    # Pin the entry's key set: the JSON shape is the contract kboat-distill Phase C
    # consumes, so drift (e.g. resurrecting the dropped isbn/asin) must fail here.
    assert set(out["kindles"]["ripe"][0]) == {"slug", "path", "title", "distilled_date"}
    assert out["counts"]["kindles_total"] == 3
    assert out["counts"]["kindles_ripe"] == 1
    assert out["counts"]["kindles_already_distilled"] == 1


def test_kindle_no_disk_writes(vault: Path, capsys):
    # Kindle has no cooldown clock; the tool must never rewrite a Kindle note.
    kindles = vault / "Kindles"
    kindles.mkdir()
    write_kindle(kindles, "B001RIPE", distill=True)
    before = (kindles / "B001RIPE.md").read_text()
    run(vault, capsys)
    assert (kindles / "B001RIPE.md").read_text() == before


def test_non_kindle_note_is_an_anomaly(vault: Path, capsys):
    kindles = vault / "Kindles"
    kindles.mkdir()
    (kindles / "weird.md").write_text("---\ntype: source\n---\n", encoding="utf-8")
    out = run(vault, capsys)
    assert any(a["path"] == "Kindles/weird.md" for a in out["anomalies"])


def test_missing_vault_errors(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        main(["--vault", str(tmp_path / "nope"), "--today", "2026-06-15"])
    assert exc.value.code != 0
