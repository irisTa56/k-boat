"""End-to-end CLI tests over a temporary vault."""

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from kboat.frontmatter import parse_frontmatter
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
    # Both folders this CLI reads are in the vault's required set, so a vault
    # holding only sources still has an empty `Kindles/`.
    (tmp_path / "Sources").mkdir()
    (tmp_path / "Kindles").mkdir()
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


def _repeat_a_line(note: Path, line: str) -> None:
    text = note.read_text(encoding="utf-8")
    note.write_text(text.replace(f"{line}\n", f"{line}\n{line}\n", 1), encoding="utf-8")


def test_a_note_naming_a_key_twice_is_held_out_of_every_work_set(vault: Path, capsys):
    # Each work set's pass ends in a note write, which the note writer refuses on
    # a note naming a key twice. A dismissed source would lose its notebook and
    # keep an id naming nothing; a ripe one would be distilled again on every run
    # without ever being stamped. So each is held back as an anomaly instead,
    # until a human repairs the note.
    sources, kindles = vault / "Sources", vault / "Kindles"
    write_note(sources, "drop", dismiss=True, filed_date="2026-06-01")
    write_note(sources, "ripe", distill=True, filed_date="2026-06-01")
    write_note(sources, "gap", summary="", topics=())
    write_kindle(kindles, "B001RIPE", distill=True)
    for note in (sources / "drop.md", sources / "ripe.md", sources / "gap.md"):
        _repeat_a_line(note, "reading: false")
    _repeat_a_line(kindles / "B001RIPE.md", "added_date: 2026-06-01")
    out = run(vault, capsys)

    assert out["phase_b"] == {"ripe": [], "dismiss_discard": []}
    assert out["needs_summary"] == []
    assert out["kindles"]["ripe"] == []
    counts = out["counts"]
    assert (counts["ripe"], counts["dismiss_discard"], counts["needs_summary"]) == (0, 0, 0)
    assert counts["kindles_ripe"] == 0
    held = {a["path"]: a["error"] for a in out["anomalies"]}
    assert sorted(held) == [
        "Kindles/B001RIPE.md",
        "Sources/drop.md",
        "Sources/gap.md",
        "Sources/ripe.md",
    ]
    assert "'reading'" in held["Sources/ripe.md"]
    assert "'added_date'" in held["Kindles/B001RIPE.md"]


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


def test_a_source_note_that_does_not_parse_is_an_anomaly(vault: Path, capsys):
    # Dropped silently, the note would only be missing from every work set.
    write_note(vault / "Sources", "ok", distill=True)
    (vault / "Sources" / "broken.md").write_text("no frontmatter here\n", encoding="utf-8")
    out = run(vault, capsys)
    assert [a["path"] for a in out["anomalies"]] == ["Sources/broken.md"]
    assert [s["slug"] for s in out["phase_a"]["stamped"]] == ["ok"]


def test_a_filed_date_it_could_not_write_is_an_anomaly(vault: Path, capsys):
    # A note with no `filed_date:` line to rewrite: the stamp refuses, and the
    # note's cooldown never starts unless the report says so.
    sources = vault / "Sources"
    write_note(sources, "a", distill=True)
    text = (sources / "a.md").read_text(encoding="utf-8").replace("filed_date:\n", "")
    (sources / "a.md").write_text(text, encoding="utf-8")
    out = run(vault, capsys)
    assert [a["path"] for a in out["anomalies"]] == ["Sources/a.md"]
    assert out["anomalies"][0]["error"].startswith("filed_date write failed")


def test_a_filed_date_named_twice_is_an_anomaly_and_not_a_stamp(vault: Path, capsys):
    # Every reader takes the last `filed_date` line, which is empty, so the source
    # is due its stamp. Written onto the first line, the stamp would land where no
    # reader looks and the cooldown would never start.
    sources = vault / "Sources"
    write_note(sources, "a", distill=True)
    text = (sources / "a.md").read_text(encoding="utf-8")
    text = text.replace("filed_date:\n", "filed_date: 2026-01-01\nfiled_date:\n")
    (sources / "a.md").write_text(text, encoding="utf-8")

    out = run(vault, capsys)

    assert [a["path"] for a in out["anomalies"]] == ["Sources/a.md"]
    assert out["anomalies"][0]["error"].startswith("filed_date write failed")
    assert out["phase_a"]["stamped"] == []
    assert out["counts"]["filed_stamped"] == 0
    fm = parse_frontmatter((sources / "a.md").read_text(encoding="utf-8"))
    assert fm["filed_date"] is None  # the note still says it was never stamped


def test_a_clear_it_could_not_write_is_not_reported_as_cleared(vault: Path, capsys):
    sources = vault / "Sources"
    write_note(sources, "a", filed_date="2026-06-01")
    text = (sources / "a.md").read_text(encoding="utf-8")
    text = text.replace("filed_date: 2026-06-01\n", "filed_date:\nfiled_date: 2026-06-01\n")
    (sources / "a.md").write_text(text, encoding="utf-8")

    out = run(vault, capsys)

    assert [a["path"] for a in out["anomalies"]] == ["Sources/a.md"]
    assert out["phase_a"]["cleared"] == []
    assert out["counts"]["filed_cleared"] == 0


def test_a_note_that_turns_unreadable_between_load_and_stamp_is_an_anomaly(
    vault: Path, capsys, monkeypatch
):
    # The vault lock is advisory, so the note can change between the plan's read and the stamp's.
    sources = vault / "Sources"
    write_note(sources, "a", distill=True)
    real_read_text = Path.read_text
    reads: list[Path] = []

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "a.md":
            reads.append(self)
            if len(reads) > 1:  # the rewrite's read, after the plan's
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return real_read_text(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    out = run(vault, capsys)

    assert [a["path"] for a in out["anomalies"]] == ["Sources/a.md"]
    assert out["anomalies"][0]["error"].startswith("filed_date write failed")


def test_a_note_that_is_not_utf8_is_an_anomaly_and_not_a_dead_pass(vault: Path, capsys):
    (vault / "Sources" / "bad.md").write_bytes(b"---\ntype: source\ntitle: \xff\n---\n")
    (vault / "Kindles" / "B0BAD.md").write_bytes(b"---\ntype: kindle\ntitle: \xff\n---\n")
    out = run(vault, capsys)
    assert sorted(a["path"] for a in out["anomalies"]) == ["Kindles/B0BAD.md", "Sources/bad.md"]


def run_unread(vault: Path, capsys, *extra: str) -> dict:
    """A run that could not read a required folder: exit 1, and the report still printed."""
    rc = main(["--vault", str(vault), "--today", "2026-06-15", *extra])
    assert rc == 1
    return json.loads(capsys.readouterr().out)


def test_an_absent_kindles_dir_fails_the_run_and_the_sources_are_still_worked(vault: Path, capsys):
    # Read as "no Kindle notes", an unsynced vault would be distilled as though
    # whole. The source half is decided from its own notes, so it is still done.
    (vault / "Kindles").rmdir()
    write_note(vault / "Sources", "a", distill=True)
    out = run_unread(vault, capsys)
    [entry] = out["anomalies"]
    assert entry["path"] == "Kindles"
    assert entry["error"].startswith("absent: ")
    assert [s["slug"] for s in out["phase_a"]["stamped"]] == ["a"]
    assert "filed_date: 2026-06-15" in (vault / "Sources" / "a.md").read_text()


def test_an_absent_sources_dir_is_reported_in_the_json_not_by_argparse(vault: Path, capsys):
    (vault / "Sources").rmdir()
    out = run_unread(vault, capsys, "--dry-run")
    assert [(a["path"], a["error"].split(":")[0]) for a in out["anomalies"]] == [
        ("Sources", "absent")
    ]
    assert out["counts"]["sources_total"] == 0


def test_a_sources_name_held_by_a_file_is_not_a_directory(vault: Path, capsys):
    (vault / "Sources").rmdir()
    (vault / "Sources").write_text("not a folder\n", encoding="utf-8")
    out = run_unread(vault, capsys, "--dry-run")
    [entry] = out["anomalies"]
    assert entry["path"] == "Sources"
    assert entry["error"].startswith("not a directory: ")


def test_a_vault_that_is_a_regular_file_reports_every_folder_as_not_a_directory(
    tmp_path: Path, capsys
):
    # The `NotADirectoryError` route: the *parent* is the file. A `Sources` that is
    # itself a file is the case above, and would pass whether or not this one is
    # handled.
    vault = tmp_path / "vault"
    vault.write_text("mis-typed --vault\n", encoding="utf-8")
    out = run_unread(vault, capsys, "--dry-run")
    assert [(a["path"], a["error"].split(":")[0]) for a in out["anomalies"]] == [
        ("Sources", "not a directory"),
        ("Kindles", "not a directory"),
    ]


def test_a_sources_dir_the_os_will_not_list_is_refused(vault: Path, capsys):
    kindles = vault / "Kindles"
    write_kindle(kindles, "B001RIPE", distill=True)
    sources = vault / "Sources"
    sources.chmod(0o000)
    try:
        out = run_unread(vault, capsys, "--dry-run")
    finally:
        sources.chmod(0o755)
    [entry] = out["anomalies"]
    assert entry["path"] == "Sources"
    assert entry["error"].startswith("refused: ")
    assert [k["slug"] for k in out["kindles"]["ripe"]] == ["B001RIPE"]


def test_an_unreadable_vault_root_is_not_reported_as_a_missing_sources(vault: Path, capsys):
    # `Sources/` is there; the root above it will not be traversed. An `is_dir()`
    # gate answered "no Sources/ directory" here and ended the run with no JSON.
    vault.chmod(0o000)
    try:
        out = run_unread(vault, capsys, "--dry-run")
    finally:
        vault.chmod(0o755)
    assert [(a["path"], a["error"].split(":")[0]) for a in out["anomalies"]] == [
        ("Sources", "refused"),
        ("Kindles", "refused"),
    ]


def test_an_evicted_source_note_is_an_anomaly_not_a_note_that_is_not_there(vault: Path, capsys):
    # iCloud placeholders are fabricated: none exists on a test machine.
    write_note(vault / "Sources", "a", distill=True)
    (vault / "Sources" / ".b.md.icloud").write_bytes(b"")
    out = run(vault, capsys, "--dry-run")
    assert [a["path"] for a in out["anomalies"]] == ["Sources/.b.md.icloud"]
    assert [s["slug"] for s in out["phase_a"]["stamped"]] == ["a"]


def test_kindle_ripe_selection(vault: Path, capsys):
    kindles = vault / "Kindles"
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
    write_kindle(kindles, "B001RIPE", distill=True)
    before = (kindles / "B001RIPE.md").read_text()
    run(vault, capsys)
    assert (kindles / "B001RIPE.md").read_text() == before


def test_non_kindle_note_is_an_anomaly(vault: Path, capsys):
    kindles = vault / "Kindles"
    (kindles / "weird.md").write_text("---\ntype: source\n---\n", encoding="utf-8")
    out = run(vault, capsys)
    assert any(a["path"] == "Kindles/weird.md" for a in out["anomalies"])


def test_a_kindle_note_that_does_not_parse_is_an_anomaly(vault: Path, capsys):
    kindles = vault / "Kindles"
    write_kindle(kindles, "B001RIPE", distill=True)
    (kindles / "broken.md").write_text("no frontmatter here\n", encoding="utf-8")
    out = run(vault, capsys)
    assert [a["path"] for a in out["anomalies"]] == ["Kindles/broken.md"]
    assert [k["slug"] for k in out["kindles"]["ripe"]] == ["B001RIPE"]


def test_a_missing_vault_is_the_lock_that_cannot_be_taken(tmp_path: Path, capsys):
    # An applying run takes the lock before it reads, so a root that is not there
    # is the lock's own failure: stderr, and nothing on stdout to parse.
    rc = main(["--vault", str(tmp_path / "nope"), "--today", "2026-06-15"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "vault lock unavailable" in captured.err
    assert captured.out == ""


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
