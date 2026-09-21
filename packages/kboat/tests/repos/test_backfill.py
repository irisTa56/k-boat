"""Tests for `kboat-repos backfill` — `readme: unknown` and `gone: false` where missing.

Each fixture note is written by the real writer and then has its `readme` and
`gone` lines taken out, so it is the note a catalogue written before both fields
holds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from kboat.frontmatter import parse_frontmatter
from kboat.lock import vault_lock
from kboat.naming import note_slug
from kboat.repos import backfill as backfill_mod
from kboat.repos.__main__ import main
from kboat.repos.write import write_note
from kboat.validate.core import check_note


def _record(owner_repo: str) -> dict[str, Any]:
    url = f"https://github.com/{owner_repo}"
    return {
        "slug": note_slug(url),
        "url": url,
        "title": owner_repo,
        "fields": {"description": "desc", "status": "recent"},
        "role": "library",
        "domain": ["devtools"],
        "summary": "要約。",
        "readme_error": None,
    }


def _legacy_note(vault: Path, owner_repo: str = "owner/repo") -> Path:
    """A repo note as the catalogue held it before `readme` and `gone` existed."""
    record = _record(owner_repo)
    write_note(record, vault, today_iso="2026-06-06")
    path = vault / "Repos" / f"{record['slug']}.md"
    text = path.read_text()
    assert "readme: fetched\n" in text and "gone: false\n" in text
    path.write_text(text.replace("readme: fetched\n", "").replace("gone: false\n", ""))
    return path


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    rc = main(["backfill", *argv])
    return rc, json.loads(capsys.readouterr().out)


def test_apply_gives_a_note_that_predates_the_fields_their_defaults(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _legacy_note(tmp_path)
    before = path.read_text()

    rc, report = _run(["--apply", "--vault", str(tmp_path)], capsys)

    assert rc == 0
    after = path.read_text()
    fm = parse_frontmatter(after)
    assert fm["readme"] == "unknown"
    assert fm["gone"] is False
    # The two lines are the whole change: in their schema positions, with nothing
    # else re-rendered — `refreshed_date` above all, since nothing was refreshed.
    assert after == before.replace(
        "summary: 要約。\n", "summary: 要約。\nreadme: unknown\n"
    ).replace("reading: false\n", "reading: false\ngone: false\n")
    assert check_note("repo", dict(fm), "p") == []
    assert report["counts"] == {"total": 1, "marked": 1, "failed": 0, "anomalies": 0}
    assert report["marked"] == [path.relative_to(tmp_path).as_posix()]


def test_a_note_missing_only_one_field_gets_only_that_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The catalogue #130 already backfilled holds `readme` and lacks only `gone`;
    # its mark must come through untouched while `gone` is added.
    record = {**_record("owner/repo"), "readme_error": "HTTP 403: rate limited"}
    write_note(record, tmp_path, today_iso="2026-06-06")
    path = tmp_path / "Repos" / f"{record['slug']}.md"
    path.write_text(path.read_text().replace("gone: false\n", ""))

    rc, report = _run(["--apply", "--vault", str(tmp_path)], capsys)

    assert rc == 0
    fm = parse_frontmatter(path.read_text())
    assert fm["readme"] == "unavailable"
    assert fm["gone"] is False
    assert report["counts"]["marked"] == 1


def test_a_ticked_gone_is_never_reset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The human's tick is the one value here that is known; writing the default
    # over it would put the note back into the refresh they took it out of.
    path = _legacy_note(tmp_path)
    path.write_text(path.read_text().replace("reading: false\n", "reading: false\ngone: true\n"))

    _run(["--apply", "--vault", str(tmp_path)], capsys)

    fm = parse_frontmatter(path.read_text())
    assert fm["gone"] is True
    assert fm["readme"] == "unknown"


def test_a_note_that_already_carries_a_mark_keeps_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A mark `kboat-repos write` set is the one thing here that is known; the
    # backfill must never downgrade it to `unknown`.
    record = {**_record("owner/repo"), "readme_error": "HTTP 403: rate limited"}
    write_note(record, tmp_path, today_iso="2026-06-06")
    path = tmp_path / "Repos" / f"{record['slug']}.md"
    before = path.read_text()

    rc, report = _run(["--apply", "--vault", str(tmp_path)], capsys)

    assert rc == 0
    assert path.read_text() == before
    assert parse_frontmatter(before)["readme"] == "unavailable"
    assert report["counts"]["marked"] == 0


def test_a_readme_line_the_reader_cannot_decode_is_still_a_mark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Adding a second `readme` beside it would leave the note holding two, and
    # whichever the reader took, `kboat-validate` could not report the first.
    path = _legacy_note(tmp_path)
    path.write_text(path.read_text().replace("summary: 要約。\n", 'summary: 要約。\n"readme": x\n'))

    _run(["--apply", "--vault", str(tmp_path)], capsys)

    after = path.read_text()
    assert after.count("readme") == 1
    assert '"readme": x\n' in after
    assert "gone: false\n" in after  # the other field is still owed


def test_a_summary_spanning_several_lines_keeps_its_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A summary edited in Obsidian can become a block scalar. The mark goes after
    # the whole entry, not after its first line, or it would cut the block in two.
    path = _legacy_note(tmp_path)
    block = "summary: >-\n  一行目\n  二行目\n"
    path.write_text(path.read_text().replace("summary: 要約。\n", block))

    _run(["--apply", "--vault", str(tmp_path)], capsys)

    assert block + "readme: unknown\n" in path.read_text()


def test_dry_run_reports_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _legacy_note(tmp_path)
    before = path.read_text()

    rc, report = _run(["--dry-run", "--vault", str(tmp_path)], capsys)

    assert rc == 0
    assert path.read_text() == before
    assert report["dry_run"] is True
    assert report["marked"] == [path.relative_to(tmp_path).as_posix()]


def test_a_second_apply_has_nothing_to_mark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _legacy_note(tmp_path)
    _run(["--apply", "--vault", str(tmp_path)], capsys)

    _, report = _run(["--apply", "--vault", str(tmp_path)], capsys)

    assert report["counts"]["marked"] == 0


def test_what_is_not_a_readable_repo_note_is_an_anomaly_and_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # An evicted note is marked by a re-run once it is back; a note of another
    # type or with no frontmatter is not this command's to touch.
    _legacy_note(tmp_path)
    repos = tmp_path / "Repos"
    (repos / ".evicted.md.icloud").write_bytes(b"placeholder")
    (repos / "stray.md").write_text("---\ntype: source\n---\n")
    (repos / "broken.md").write_text("no frontmatter here\n")

    rc, report = _run(["--apply", "--vault", str(tmp_path)], capsys)

    assert rc == 0
    assert sorted(a["path"] for a in report["anomalies"]) == [
        "Repos/.evicted.md.icloud",
        "Repos/broken.md",
        "Repos/stray.md",
    ]
    assert report["counts"]["total"] == 1
    assert (repos / "stray.md").read_text() == "---\ntype: source\n---\n"


def test_a_note_naming_a_key_twice_is_left_for_a_human(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Re-assembling the note would keep only the second `summary`, deleting the
    # line a human editing the note sees, with nothing to say which was meant.
    path = _legacy_note(tmp_path)
    path.write_text(path.read_text().replace("summary: 要約。\n", "summary: 一\nsummary: 二\n"))
    before = path.read_text()

    rc, report = _run(["--apply", "--vault", str(tmp_path)], capsys)

    assert rc == 1
    assert path.read_text() == before
    assert report["failed"] == [
        {"path": path.relative_to(tmp_path).as_posix(), "error": "note names 'summary' on 2 lines"}
    ]
    assert report["marked"] == []


def test_a_write_that_fails_is_reported_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _legacy_note(tmp_path)
    before = path.read_text()

    def refuse(target: Path, text: str) -> None:
        raise PermissionError(1, "Operation not permitted", str(target))

    monkeypatch.setattr(backfill_mod, "atomic_write_text", refuse)
    rc, report = _run(["--apply", "--vault", str(tmp_path)], capsys)

    assert rc == 1
    assert [f["path"] for f in report["failed"]] == [path.relative_to(tmp_path).as_posix()]
    assert report["marked"] == []
    assert path.read_text() == before


def test_a_vault_with_no_repos_folder_exits_nonzero_with_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, report = _run(["--dry-run", "--vault", str(tmp_path)], capsys)

    assert rc == 1
    assert [a["path"] for a in report["anomalies"]] == ["Repos"]


def test_apply_refuses_a_locked_vault(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], brief_lock_wait: None
) -> None:
    path = _legacy_note(tmp_path)
    before = path.read_text()

    with vault_lock(tmp_path):
        rc, out = _run(["--apply", "--vault", str(tmp_path)], capsys)

    assert rc == 1
    assert out["status"] == "locked"
    assert out["holder"]["pid"] == os.getpid()
    assert path.read_text() == before


def test_dry_run_reads_a_locked_vault(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _legacy_note(tmp_path)

    with vault_lock(tmp_path):
        rc, report = _run(["--dry-run", "--vault", str(tmp_path)], capsys)

    assert rc == 0
    assert report["counts"]["marked"] == 1


def test_a_mode_is_required(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["backfill", "--vault", str(tmp_path)])
    assert excinfo.value.code == 2
