"""End-to-end tests for the `kboat-pick` CLI."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from kboat.lock import vault_lock
from kboat.pick.__main__ import main
from kboat.pick.notes import Value, parse_frontmatter


def _source(
    slug: str,
    *,
    source_type: str = "web_page",
    picked: bool = False,
    reading: bool = False,
    **flags: bool,
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
        f"reading: {str(reading).lower()}\n"
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
    # An in-progress read that was picked on a prior run (started since): no longer
    # a candidate, yet its stale `picked` must still be cleared by `set`.
    (sources / "reading1.md").write_text(
        _source("reading1", reading=True, picked=True), encoding="utf-8"
    )
    daily = tmp_path / "Daily"
    daily.mkdir()
    (daily / "2026-06-04.md").write_text(
        "---\ntags: [daily]\n---\n\ncurious about agentic workflows\n", encoding="utf-8"
    )
    (tmp_path / "Questions.md").write_text(
        "- how do agents plan?\n    - the deliberate signal\n- what is a MoE?\n",
        encoding="utf-8",
    )
    return tmp_path


def test_candidates_lists_only_active_web_plus_daily_notes(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--vault", str(vault), "candidates", "--today", "2026-06-12"]) == 0
    out = json.loads(capsys.readouterr().out)
    slugs = {c["slug"] for c in out["candidates"]}
    # kept (disposition), doc (pdf), and reading1 (in-progress) all excluded.
    assert slugs == {"web1", "web2"}
    assert out["counts"]["candidates_total"] == 2
    # Frontmatter is stripped; the body carries the human's interest signal.
    assert out["daily_notes"] == [{"date": "2026-06-04", "body": "curious about agentic workflows"}]
    assert out["counts"]["daily_note_days"] == 1
    # The open-questions backlog: ordered by list position (rank 1 = top interest),
    # each with its nested sub-bullet as the note.
    assert out["questions"] == [
        {"rank": 1, "question": "how do agents plan?", "note": "the deliberate signal"},
        {"rank": 2, "question": "what is a MoE?", "note": ""},
    ]
    assert out["counts"]["questions_total"] == 2
    assert out["candidates"][0]["topics"] == ["topic-web1"]
    # `added_date` (the diversification key) and `notebooklm_id` (the Stage 2
    # fulltext handle) survive the parser → candidate → JSON round-trip.
    assert out["candidates"][0]["added_date"] == "2026-06-01"
    assert out["candidates"][0]["notebooklm_id"] == "id-web1"
    assert out["lookback_days"] == 14  # default window


def test_a_source_note_that_is_not_utf8_is_an_anomaly_and_not_a_dead_gather(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (vault / "Sources" / "bad.md").write_bytes(b"---\ntype: source\ntitle: \xff\n---\n")
    assert main(["--vault", str(vault), "candidates", "--today", "2026-06-12"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [a["path"] for a in out["anomalies"]] == ["Sources/bad.md"]


def test_an_unreadable_questions_file_and_daily_note_are_anomalies_not_silence(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (vault / "Questions.md").write_bytes(b"- what about \xff\n")
    (vault / "Daily" / "2026-06-11.md").write_bytes(b"\xff\n")
    assert main(["--vault", str(vault), "candidates", "--today", "2026-06-12"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert sorted(a["path"] for a in out["anomalies"]) == ["Daily/2026-06-11.md", "Questions.md"]
    assert out["questions"] == []


def test_candidates_lookback_window_drops_stale_notes(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The fixture's only note is dated 2026-06-04; an 8-day-back date with a
    # 3-day window is out of scope, so no daily notes surface (candidates still do).
    args = ["--vault", str(vault), "candidates", "--today", "2026-06-12", "--lookback-days", "3"]
    assert main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["daily_notes"] == []
    assert out["lookback_days"] == 3
    assert {c["slug"] for c in out["candidates"]} == {"web1", "web2"}


def test_candidates_rejects_negative_lookback(vault: Path) -> None:
    args = ["--vault", str(vault), "candidates", "--today", "2026-06-12", "--lookback-days", "-1"]
    with pytest.raises(SystemExit) as exc:  # argparse parser.error exits with code 2
        main(args)
    assert exc.value.code == 2


def test_set_marks_chosen_and_resets_rest(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--vault", str(vault), "set", "--slugs", "web1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["picked"] == ["web1"]
    assert out["reset"] == 4  # web2, kept, doc, reading1
    assert out["missing"] == []

    def fm(name: str) -> dict[str, Value]:
        return parse_frontmatter((vault / "Sources" / name).read_text(encoding="utf-8"))

    assert fm("web1.md")["picked"] is True
    assert fm("web2.md")["picked"] is False
    assert fm("kept.md")["picked"] is False
    # The in-progress read is excluded from candidates yet still gets its stale
    # `picked` reset, since `set` resets every source unconditionally.
    assert fm("reading1.md")["picked"] is False


def test_set_reports_missing_slug(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--vault", str(vault), "set", "--slugs", "web1,ghost"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["picked"] == ["web1"]
    assert out["missing"] == ["ghost"]


def test_candidates_reports_a_source_note_it_cannot_parse(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Dropped silently, the note would only be absent from a pick that reads whole.
    (vault / "Sources" / "broken.md").write_text("no frontmatter here\n", encoding="utf-8")
    assert main(["--vault", str(vault), "candidates", "--today", "2026-06-12"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [a["path"] for a in out["anomalies"]] == ["Sources/broken.md"]
    assert {c["slug"] for c in out["candidates"]} == {"web1", "web2"}


def test_set_reports_a_picked_flag_it_could_not_write(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A `Sources/` that refuses new files fails the atomic rewrite of both notes whose
    # flag changes. Unreported, the new pick would be neither picked nor missing,
    # and the stale one would stand.
    sources = vault / "Sources"
    sources.chmod(0o555)
    try:
        assert main(["--vault", str(vault), "set", "--slugs", "web1"]) == 0
    finally:
        sources.chmod(0o755)
    out = json.loads(capsys.readouterr().out)
    assert out["picked"] == []
    assert out["missing"] == []
    paths = sorted(a["path"] for a in out["anomalies"])
    assert paths == ["Sources/reading1.md", "Sources/web1.md"]
    assert all(a["error"].startswith("picked write failed") for a in out["anomalies"])


def test_set_reports_a_note_that_turns_unreadable_between_load_and_write(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The vault lock is advisory, so a note can change between the load's read and the write's.
    real_read_text = Path.read_text
    reads: list[Path] = []

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "web2.md":
            reads.append(self)
            if len(reads) > 1:  # the write loop's read, after `_load_sources`'s
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return real_read_text(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    assert main(["--vault", str(vault), "set", "--slugs", "web1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [a["path"] for a in out["anomalies"]] == ["Sources/web2.md"]
    assert out["anomalies"][0]["error"].startswith("picked write failed")


def test_empty_slugs_clears_all(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--vault", str(vault), "set", "--slugs", "web1"])
    capsys.readouterr()
    assert main(["--vault", str(vault), "set", "--slugs", ""]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["picked"] == []
    assert out["reset"] == 5
    fm = parse_frontmatter((vault / "Sources" / "web1.md").read_text(encoding="utf-8"))
    assert fm["picked"] is False


def test_set_refuses_a_locked_vault_without_writing(
    vault: Path, capsys: pytest.CaptureFixture[str], brief_lock_wait: None
) -> None:
    before = (vault / "Sources" / "web1.md").read_text(encoding="utf-8")
    with vault_lock(vault):
        rc = main(["--vault", str(vault), "set", "--slugs", "web1"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "locked"
    assert out["holder"]["pid"] == os.getpid()
    assert (vault / "Sources" / "web1.md").read_text(encoding="utf-8") == before


def test_candidates_reads_a_locked_vault(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Read-only, so it neither takes the lock nor waits on one.
    with vault_lock(vault):
        assert main(["--vault", str(vault), "candidates", "--today", "2026-06-12"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [c["slug"] for c in out["candidates"]] == ["web1", "web2"]


def test_the_picked_flag_goes_through_the_atomic_writer(vault: Path) -> None:
    # As in `kboat-lifecycle`: the flag is flipped by replacing the note, never by
    # writing over it, so a rewrite that stopped using `os.replace` would silently
    # give up atomicity on a note the routine touches every day.
    replaced: list[str] = []
    real_replace = os.replace

    def spy(src: object, dst: object) -> None:
        replaced.append(str(dst))
        real_replace(src, dst)  # ty: ignore[invalid-argument-type]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "replace", spy)
        assert main(["--vault", str(vault), "set", "--slugs", "web1"]) == 0

    sources = vault / "Sources"
    # Exactly the two notes whose `picked` changed — the new pick and the stale one
    # being cleared — and each of them by a replace rather than a write in place.
    assert sorted(replaced) == sorted([str(sources / "web1.md"), str(sources / "reading1.md")])
    assert parse_frontmatter((sources / "web1.md").read_text())["picked"] is True
    assert parse_frontmatter((sources / "reading1.md").read_text())["picked"] is False


def test_the_sources_scan_happens_inside_the_hold(
    vault: Path, lock_is_held: Callable[[Path], bool]
) -> None:
    # As in `kboat-lifecycle`: `set` reads every source to decide which notes
    # change, and that read belongs under the same hold as the writes it feeds.
    sources = vault / "Sources"
    held: list[bool] = []
    real_read_text = Path.read_text

    def spy(self: Path, *args: object, **kwargs: object) -> str:
        if self.parent == sources:
            held.append(lock_is_held(vault))
        return real_read_text(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_text", spy)
        assert main(["--vault", str(vault), "set", "--slugs", "web1"]) == 0

    assert held and all(held), "every Sources/ read must happen while the lock is held"


def test_a_vault_whose_lock_cannot_be_opened_is_reported_not_dumped(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # As in `kboat-lifecycle`: reported on stderr with an empty stdout, never a
    # traceback, and without a `locked` record that would invite a retry.
    vault.chmod(0o555)
    try:
        rc = main(["--vault", str(vault), "set", "--slugs", "web1"])
    finally:
        vault.chmod(0o755)
    assert rc == 1
    captured = capsys.readouterr()
    assert "vault lock unavailable" in captured.err
    assert captured.out == ""
