"""End-to-end tests for the `kboat-validate` CLI."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from kboat.validate.__main__ import main

VALID_SOURCE = """\
---
type: source
title: Clean
reading_link: https://example.com/a
reading: false
distill: false
keep: false
dismiss: false
source_type: web_page
url: https://example.com/a
summary: ok
topics:
  - x
added_date: 2026-06-01
filed_date:
distilled_date:
blocked: false
picked: false
notebooklm_id: id
notebooklm_url: https://n
tags: []
---
"""

# Missing the required `picked` boolean, and a bad `source_type`.
BAD_SOURCE = VALID_SOURCE.replace("picked: false\n", "").replace(
    "source_type: web_page", "source_type: podcast"
)


def _vault(tmp_path: Path, **notes: str) -> Path:
    for sub in ("Sources", "Kindles", "Repos"):
        (tmp_path / sub).mkdir()
    for name, text in notes.items():
        (tmp_path / "Sources" / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_clean_vault_reports_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path, **{"a.md": VALID_SOURCE})
    assert main(["--vault", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checked"] == {"source": 1, "kindle": 0, "repo": 0, "feed": 0}
    assert out["counts"]["total"] == 0


def test_violations_reported_and_strict_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path, **{"bad.md": BAD_SOURCE})
    # Default: report-only, exit 0.
    assert main(["--vault", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    codes = {v["code"] for v in out["violations"]}
    assert "missing_field" in codes  # picked
    assert "bad_enum" in codes  # source_type
    assert out["counts"]["total"] >= 2

    # --strict: same findings, non-zero exit.
    assert main(["--vault", str(vault), "--strict"]) == 1


def test_parse_error_is_a_violation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path, **{"broken.md": "no frontmatter here\n"})
    main(["--vault", str(vault)])
    out = json.loads(capsys.readouterr().out)
    assert any(v["code"] == "parse_error" for v in out["violations"])


@pytest.mark.parametrize(
    "held", ["picked: true\npicked: false\n", '"picked": true\npicked: false\n']
)
def test_a_key_named_twice_is_a_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], held: str
) -> None:
    # Every field reads as valid — the reader keeps the last line — so without this
    # the note is clean here while every run that writes `picked` refuses it.
    vault = _vault(tmp_path, **{"a.md": VALID_SOURCE.replace("picked: false\n", held)})
    assert main(["--vault", str(vault), "--strict"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [(v["path"], v["field"], v["code"]) for v in out["violations"]] == [
        ("Sources/a.md", "picked", "repeated_key")
    ]


def test_a_note_that_is_not_utf8_is_a_violation_and_not_a_dead_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path, **{"a.md": VALID_SOURCE})
    (vault / "Sources" / "bad.md").write_bytes(b"---\ntype: source\ntitle: \xff\n---\n")
    assert main(["--vault", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checked"]["source"] == 2
    assert [v["path"] for v in out["violations"] if v["code"] == "parse_error"] == [
        "Sources/bad.md"
    ]


def test_an_evicted_note_is_a_violation_rather_than_a_shorter_backlog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # It matches no `*.md` glob, so without the placeholder sweep the vault reports
    # a clean, shorter backlog — the counts feed a notification threshold, so the
    # wrong answer here is silence rather than a wrong number.
    vault = _vault(tmp_path, **{"clean.md": VALID_SOURCE})
    (vault / "Sources" / ".evicted.md.icloud").write_bytes(b"")

    exit_code = main(["--vault", str(vault), "--strict", "--stats", "--today", "2026-06-15"])
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    evicted = [v for v in out["violations"] if v["code"] == "icloud_placeholder"]
    assert [v["path"] for v in evicted] == ["Sources/.evicted.md.icloud"]
    assert out["checked"]["source"] == 1, "the placeholder is not counted as a note"


def test_a_note_directory_that_cannot_be_listed_is_a_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `Path.glob` hands back an empty directory here, so without this the report
    # would be a clean vault with an empty backlog — the same silence an eviction
    # produces, and the one a threshold on the counts cannot see.
    vault = _vault(tmp_path, **{"clean.md": VALID_SOURCE})
    (vault / "Sources").chmod(0o111)
    try:
        exit_code = main(["--vault", str(vault), "--strict", "--stats", "--today", "2026-06-15"])
    finally:
        (vault / "Sources").chmod(0o755)
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    unreadable = [v for v in out["violations"] if v["code"] == "unreadable_dir"]
    assert [v["path"] for v in unreadable] == ["Sources"]
    assert out["checked"]["source"] == 0


def test_an_unparseable_note_is_a_violation_but_not_a_stat(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing readable to count, so it reaches no stat — the same rule as a
    # misfiled note, and for the same reason: a count no run could act on.
    vault = _vault(tmp_path, **{"broken.md": "no frontmatter here\n"})
    main(["--vault", str(vault), "--stats", "--today", "2026-06-15"])
    out = json.loads(capsys.readouterr().out)
    assert any(v["code"] == "parse_error" for v in out["violations"])
    assert out["stats"] == {
        "blocked_count": 0,
        "blocked_oldest_age_days": None,
        "stalled_summaries": 0,
        "summary_unrecoverable": 0,
        "ripe_undistilled": 0,
        "ripe_undistilled_kindles": 0,
        "awaiting_filed_stamp": 0,
        "unrefreshed_repo_count": 0,
        "unrefreshed_repo_oldest_age_days": None,
        "queued_count": 0,
        "queued_oldest_age_days": None,
    }


# A DLQ entry (no notebook), and a source ripe for distillation.
BLOCKED_SOURCE = (
    VALID_SOURCE.replace("blocked: false", "blocked: true")
    .replace("notebooklm_id: id", "notebooklm_id:")
    .replace("added_date: 2026-06-01", "added_date: 2026-05-16")
)
RIPE_SOURCE = VALID_SOURCE.replace("distill: false", "distill: true").replace(
    "filed_date:", "filed_date: 2026-06-08"
)


def test_stats_are_absent_unless_requested(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path, **{"a.md": VALID_SOURCE})
    main(["--vault", str(vault)])
    assert "stats" not in json.loads(capsys.readouterr().out)


def test_stats_report_the_backlog(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path, **{"a.md": BLOCKED_SOURCE, "b.md": RIPE_SOURCE})
    assert main(["--vault", str(vault), "--stats", "--today", "2026-06-15"]) == 0
    stats = json.loads(capsys.readouterr().out)["stats"]
    assert stats == {
        "blocked_count": 1,
        "blocked_oldest_age_days": 30,
        "stalled_summaries": 0,
        "summary_unrecoverable": 0,
        "ripe_undistilled": 1,
        "ripe_undistilled_kindles": 0,
        "awaiting_filed_stamp": 0,
        "unrefreshed_repo_count": 0,
        "unrefreshed_repo_oldest_age_days": None,
        "queued_count": 0,
        "queued_oldest_age_days": None,
    }


def test_stats_never_change_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A backlog is not drift: only violations reach `--strict`.
    vault = _vault(tmp_path, **{"a.md": BLOCKED_SOURCE})
    assert main(["--vault", str(vault), "--stats", "--strict", "--today", "2026-06-15"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["total"] == 0
    assert out["stats"]["blocked_count"] == 1


def test_strict_still_fails_on_a_violation_alongside_stats(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The other half of the same contract: asking for stats must not soften
    # `--strict` either.
    vault = _vault(tmp_path, **{"a.md": BAD_SOURCE})
    assert main(["--vault", str(vault), "--stats", "--strict", "--today", "2026-06-15"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["total"] > 0
    assert "stats" in out


def test_stats_ignore_notes_outside_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Only source notes have this lifecycle. The planted notes carry states that
    # would each raise a count, so the assertion fails if the filter regresses.
    vault = _vault(tmp_path, **{"a.md": VALID_SOURCE})
    (vault / "Kindles" / "B00X.md").write_text(BLOCKED_SOURCE, encoding="utf-8")
    (vault / "Repos" / "abc.md").write_text(RIPE_SOURCE, encoding="utf-8")
    main(["--vault", str(vault), "--stats", "--today", "2026-06-15"])
    out = json.loads(capsys.readouterr().out)
    assert out["checked"]["kindle"] == 1
    assert out["checked"]["repo"] == 1
    assert out["stats"]["blocked_count"] == 0
    assert out["stats"]["ripe_undistilled"] == 0


def test_a_misfiled_note_in_sources_is_a_violation_but_not_a_stat(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The lifecycle loads a source by its declared `type` and calls a mismatch an
    # anomaly, so a note misfiled into `Sources/` must not become a phantom count
    # no run can drain — while still being reported as drift.
    stray = RIPE_SOURCE.replace("type: source", "type: kindle")
    vault = _vault(tmp_path, **{"stray.md": stray})
    main(["--vault", str(vault), "--stats", "--today", "2026-06-15"])
    out = json.loads(capsys.readouterr().out)
    assert out["stats"]["ripe_undistilled"] == 0
    assert any(v["field"] == "type" for v in out["violations"])


RIPE_KINDLE = """\
---
type: kindle
title: A Book
reading_link: https://read.amazon.co.jp/?asin=B00X
author:
  - Someone
store_link: https://www.amazon.co.jp/dp/B00X
published:
publisher:
reading: false
finished: true
distill: true
distilled_date:
added_date: 2026-06-01
tags: []
---
"""


def test_stats_count_a_ripe_kindle_book(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Distillation processes books too, so a stalled Phase C has to be visible.
    vault = _vault(tmp_path)
    (vault / "Kindles" / "B00X.md").write_text(RIPE_KINDLE, encoding="utf-8")
    assert main(["--vault", str(vault), "--stats", "--strict", "--today", "2026-06-15"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stats"]["ripe_undistilled_kindles"] == 1
    assert out["stats"]["ripe_undistilled"] == 0


def test_stats_read_the_summary_gaps_off_disk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The frontmatter-to-count path a real vault exercises: an empty `topics:`
    # list and a blank `notebooklm_id:` have to read as empty through the parser,
    # which is what puts each source in one gap rather than the other.
    stalled = VALID_SOURCE.replace("topics:\n  - x\n", "topics: []\n")
    lost = stalled.replace("notebooklm_id: id", "notebooklm_id:")
    vault = _vault(tmp_path, **{"a.md": stalled, "b.md": lost})
    main(["--vault", str(vault), "--stats", "--today", "2026-06-15"])
    out = json.loads(capsys.readouterr().out)
    assert out["stats"]["stalled_summaries"] == 1
    assert out["stats"]["summary_unrecoverable"] == 1


def test_the_validator_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # `--stats` reaches into the lifecycle, whose CLI sibling does write
    # `filed_date` to disk. Nothing here may: the routine runs this after the
    # distill pass, so a write would be an unreviewed second pass over the vault.
    vault = _vault(tmp_path, **{"a.md": RIPE_SOURCE, "b.md": BLOCKED_SOURCE})
    (vault / "Kindles" / "B00X.md").write_text(RIPE_KINDLE, encoding="utf-8")
    before = {
        p.relative_to(vault).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(vault.rglob("*"))
        if p.is_file()
    }
    main(["--vault", str(vault), "--stats", "--strict", "--today", "2026-06-15"])
    capsys.readouterr()
    after = {
        p.relative_to(vault).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(vault.rglob("*"))
        if p.is_file()
    }
    assert after == before


def test_a_misfiled_note_in_kindles_is_a_violation_but_not_a_stat(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The same guard on the other distillable kind.
    stray = "---\ntype: source\ntitle: Stray\ndistill: true\ndistilled_date:\n---\n"
    vault = _vault(tmp_path)
    (vault / "Kindles" / "B00X.md").write_text(stray, encoding="utf-8")
    main(["--vault", str(vault), "--stats", "--today", "2026-06-15"])
    out = json.loads(capsys.readouterr().out)
    assert out["stats"]["ripe_undistilled_kindles"] == 0
    assert any(v["field"] == "type" for v in out["violations"])


def _repo_note(refreshed: str) -> str:
    return f"---\ntype: repo\ntitle: o/r\nurl: https://github.com/o/r\nrefreshed_date: {refreshed}\n---\n"


def _capture(vault: Path, year: int, month: int, day: int, hour: int) -> None:
    """A capture named as the bookmarklet names one made at that local time."""
    queue = vault / "Queue"
    queue.mkdir(exist_ok=True)
    made = time.mktime((year, month, day, hour, 0, 0, 0, 0, -1))
    name = f"kboat-queue-{int(made * 1000)}.md"
    (queue / name).write_text("[A page](https://example.com/a)\n", encoding="utf-8")


def test_stats_age_a_repo_the_refresh_stopped_reaching_and_a_stuck_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A repo note whose refresh has failed for a month, and a capture ingest has
    # kept for a week and a day: both look like an ordinary day's retry from inside
    # any one run, and only their age says otherwise.
    vault = _vault(tmp_path)
    (vault / "Repos" / "fresh.md").write_text(_repo_note("2026-06-15"), encoding="utf-8")
    (vault / "Repos" / "stuck.md").write_text(_repo_note("2026-05-16"), encoding="utf-8")
    _capture(vault, 2026, 6, 7, 9)
    _capture(vault, 2026, 6, 15, 8)

    assert main(["--vault", str(vault), "--stats", "--today", "2026-06-15"]) == 0
    stats = json.loads(capsys.readouterr().out)["stats"]

    assert stats["unrefreshed_repo_count"] == 1
    assert stats["unrefreshed_repo_oldest_age_days"] == 30
    assert stats["queued_count"] == 2
    assert stats["queued_oldest_age_days"] == 8


def test_a_healthy_repo_catalogue_and_a_drained_queue_are_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    (vault / "Repos" / "fresh.md").write_text(_repo_note("2026-06-15"), encoding="utf-8")
    (vault / "Queue").mkdir()

    main(["--vault", str(vault), "--stats", "--today", "2026-06-15"])
    stats = json.loads(capsys.readouterr().out)["stats"]

    assert stats["unrefreshed_repo_count"] == 0
    assert stats["unrefreshed_repo_oldest_age_days"] is None
    assert stats["queued_count"] == 0
    assert stats["queued_oldest_age_days"] is None


def test_a_queue_that_cannot_be_listed_is_a_violation_rather_than_an_empty_queue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A refused listing comes back empty from a glob, and an empty queue is the
    # healthy answer — so without this a queue nobody can drain reads as drained.
    vault = _vault(tmp_path)
    _capture(vault, 2026, 6, 1, 9)
    (vault / "Queue").chmod(0o111)
    try:
        exit_code = main(["--vault", str(vault), "--strict", "--stats", "--today", "2026-06-15"])
    finally:
        (vault / "Queue").chmod(0o755)
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    unreadable = [v for v in out["violations"] if v["code"] == "unreadable_dir"]
    assert [v["path"] for v in unreadable] == ["Queue"]
