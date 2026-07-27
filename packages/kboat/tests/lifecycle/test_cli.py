"""End-to-end CLI tests over a temporary vault."""

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from kboat.lifecycle.__main__ import main
from kboat.lock import vault_lock

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


def test_refuses_a_locked_vault_without_writing(vault: Path, capsys, brief_lock_wait: None):
    # A run that cannot take the lock reports who holds it and touches nothing:
    # the note keeps its unstamped filed_date for the next run to stamp.
    sources = vault / "Sources"
    write_note(sources, "a", distill=True)
    before = (sources / "a.md").read_text(encoding="utf-8")
    with vault_lock(vault):
        rc = main(["--vault", str(vault), "--today", "2026-06-15"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "locked"
    assert out["holder"]["pid"] == os.getpid()
    assert (sources / "a.md").read_text(encoding="utf-8") == before


def test_dry_run_reads_a_locked_vault(vault: Path, capsys):
    # Read-only, so it neither takes the lock nor waits on one.
    write_note(vault / "Sources", "a", distill=True)
    with vault_lock(vault):
        out = run(vault, capsys, "--dry-run")
    assert [s["slug"] for s in out["phase_a"]["stamped"]] == ["a"]


def test_the_filed_date_stamp_goes_through_the_atomic_writer(vault: Path) -> None:
    # The whole point of routing this rewrite through the shared writer is that no
    # note is ever written in place: `os.replace` is what makes the stamp land
    # whole, and a rewrite that stopped using it would be invisible otherwise.
    sources = vault / "Sources"
    write_note(sources, "a", distill=True)
    replaced: list[str] = []
    real_replace = os.replace

    def spy(src: object, dst: object) -> None:
        replaced.append(str(dst))
        real_replace(src, dst)  # ty: ignore[invalid-argument-type]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "replace", spy)
        assert main(["--vault", str(vault), "--today", "2026-06-15"]) == 0

    assert replaced == [str(sources / "a.md")]
    assert "filed_date: 2026-06-15" in (sources / "a.md").read_text(encoding="utf-8")


def test_the_plan_is_computed_inside_the_hold(
    vault: Path, lock_is_held: Callable[[Path], bool]
) -> None:
    # The read and the write are one step so they happen under one hold: a plan
    # computed before another run's writes would stamp dates the notes no longer
    # call for. Nothing else would notice the read moving out of the block — the
    # write would still be refused, and the suite would still pass.
    sources = vault / "Sources"
    write_note(sources, "a", distill=True)
    held: list[bool] = []
    real_read_text = Path.read_text

    def spy(self: Path, *args: object, **kwargs: object) -> str:
        if self.parent == sources:
            held.append(lock_is_held(vault))
        return real_read_text(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_text", spy)
        assert main(["--vault", str(vault), "--today", "2026-06-15"]) == 0

    assert held and all(held), "every Sources/ read must happen while the lock is held"


def test_a_vault_whose_lock_cannot_be_opened_is_reported_not_dumped(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The contract is JSON on stdout and a diagnostic on stderr, never a traceback;
    # before this was caught it aborted the run with an empty stdout. A vault root that
    # cannot be written stands in for the reachable cases (a denied iCloud tree, a
    # filesystem refusing the lock) — it reaches the same failure, and is the real one
    # on the single run that creates the lock file.
    write_note(vault / "Sources", "a", distill=True)
    vault.chmod(0o555)
    try:
        rc = main(["--vault", str(vault), "--today", "2026-06-15"])
    finally:
        vault.chmod(0o755)
    assert rc == 1
    captured = capsys.readouterr()
    assert "vault lock unavailable" in captured.err
    # No `locked` record: nobody holds the vault, so this is not a run to retry.
    assert captured.out == ""
