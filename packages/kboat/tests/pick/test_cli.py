"""End-to-end tests for the `kboat-pick` CLI."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from kboat.frontmatter import repeated_keys
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


def _candidates(vault: Path, capsys: pytest.CaptureFixture[str], rc: int) -> dict:
    assert main(["--vault", str(vault), "candidates", "--today", "2026-06-12"]) == rc
    return json.loads(capsys.readouterr().out)


def _where(out: dict) -> list[tuple[str, str]]:
    """Each anomaly as its path and the word its error leads with."""
    return [(a["path"], a["error"].split(":")[0]) for a in out["anomalies"]]


def test_an_unreadable_daily_note_is_an_anomaly_and_the_pick_still_runs(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Daily notes are the ambient signal the pick degrades over, so one that could
    # not be read is reported without failing the run.
    (vault / "Daily" / "2026-06-11.md").write_bytes(b"\xff\n")
    out = _candidates(vault, capsys, 0)
    assert [a["path"] for a in out["anomalies"]] == ["Daily/2026-06-11.md"]


@pytest.mark.parametrize(
    ("make", "word"),
    [
        (lambda q: q.unlink(), "absent"),
        (lambda q: q.write_bytes(b"- what about \xff\n"), "not UTF-8"),
        (lambda q: (q.unlink(), q.mkdir()), "not a file"),
        (lambda q: (q.unlink(), (q.parent / ".Questions.md.icloud").write_bytes(b"")), "evicted"),
        (lambda q: q.chmod(0o000), "refused"),
    ],
    ids=["absent", "not-utf8", "not-a-file", "evicted", "refused"],
)
def test_a_questions_file_the_pick_cannot_read_fails_the_run_with_the_report(
    vault: Path, capsys: pytest.CaptureFixture[str], make: Callable[[Path], object], word: str
) -> None:
    # The backlog is the pick's deliberate signal: a pick made without it would
    # read exactly like one steered by it, so no way of losing it is an empty one.
    questions = vault / "Questions.md"
    make(questions)
    try:
        out = _candidates(vault, capsys, 1)
    finally:
        if questions.is_file():
            questions.chmod(0o644)
    assert _where(out) == [("Questions.md", word)]
    assert out["questions"] == []
    assert {c["slug"] for c in out["candidates"]} == {"web1", "web2"}


def test_an_absent_daily_dir_is_silent_and_does_not_fail(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for note in (vault / "Daily").iterdir():
        note.unlink()
    (vault / "Daily").rmdir()
    out = _candidates(vault, capsys, 0)
    assert out["anomalies"] == []
    assert out["daily_notes"] == []


def test_a_daily_dir_the_os_will_not_list_is_reported_without_failing(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    daily = vault / "Daily"
    daily.chmod(0o000)
    try:
        out = _candidates(vault, capsys, 0)
    finally:
        daily.chmod(0o755)
    assert _where(out) == [("Daily", "refused")]


def test_an_evicted_daily_note_is_reported_only_inside_the_look_back_window(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    daily = vault / "Daily"
    (daily / ".2026-06-10.md.icloud").write_bytes(b"")  # in the window
    (daily / ".2026-05-01.md.icloud").write_bytes(b"")  # older than the window
    (daily / ".scratch.md.icloud").write_bytes(b"")  # not a daily note at all
    out = _candidates(vault, capsys, 0)
    assert [a["path"] for a in out["anomalies"]] == ["Daily/.2026-06-10.md.icloud"]


@pytest.mark.parametrize("command", [["candidates", "--today", "2026-06-12"], ["set"]])
def test_an_absent_sources_dir_is_reported_in_the_json_not_by_argparse(
    vault: Path, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    for note in (vault / "Sources").iterdir():
        note.unlink()
    (vault / "Sources").rmdir()
    assert main(["--vault", str(vault), *command]) == 1
    out = json.loads(capsys.readouterr().out)
    assert _where(out) == [("Sources", "absent")]


@pytest.mark.parametrize("command", [["candidates", "--today", "2026-06-12"], ["set"]])
def test_a_vault_that_is_a_regular_file_reports_sources_as_not_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    # The parent is the file — the route a `Sources` that is itself a file misses.
    # `set` meets it at the lock, which cannot be taken under a file.
    vault = tmp_path / "vault"
    vault.write_text("mis-typed --vault\n", encoding="utf-8")
    assert main(["--vault", str(vault), *command]) == 1
    captured = capsys.readouterr()
    if command == ["set"]:
        assert "vault lock unavailable" in captured.err
        assert captured.out == ""
        return
    out = json.loads(captured.out)
    assert _where(out)[0] == ("Sources", "not a directory")


def test_a_sources_name_held_by_a_file_is_not_a_directory_to_set(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sources = vault / "Sources"
    for note in sources.iterdir():
        note.unlink()
    sources.rmdir()
    sources.write_text("not a folder\n", encoding="utf-8")
    assert main(["--vault", str(vault), "set", "--slugs", "web1"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert _where(out) == [("Sources", "not a directory")]
    # Not "missing": nothing was read to be missing from.
    assert out["missing"] == []


@pytest.mark.parametrize("command", [["candidates", "--today", "2026-06-12"], ["set"]])
def test_a_sources_dir_the_os_will_not_list_is_refused(
    vault: Path, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    sources = vault / "Sources"
    sources.chmod(0o000)
    try:
        assert main(["--vault", str(vault), *command]) == 1
    finally:
        sources.chmod(0o755)
    out = json.loads(capsys.readouterr().out)
    assert _where(out) == [("Sources", "refused")]


def test_an_unreadable_vault_root_is_not_reported_as_a_missing_sources(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `Sources/` is there and the root above it will not be traversed: an `is_dir()`
    # gate answered "no Sources/ directory" and ended the run with no JSON.
    vault.chmod(0o000)
    try:
        out = _candidates(vault, capsys, 1)
    finally:
        vault.chmod(0o755)
    assert ("Sources", "refused") in _where(out)
    assert ("Questions.md", "refused") in _where(out)


def test_an_evicted_source_note_is_an_anomaly_not_a_note_that_is_not_there(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (vault / "Sources" / ".web3.md.icloud").write_bytes(b"")
    out = _candidates(vault, capsys, 0)
    assert [a["path"] for a in out["anomalies"]] == ["Sources/.web3.md.icloud"]


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


def test_set_does_not_report_a_pick_no_reader_of_the_note_sees(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Two `picked` lines, and every reader takes the last. Rewriting the first would
    # leave the note reading `false` under a report that named it picked.
    note = vault / "Sources" / "web1.md"
    note.write_text(
        note.read_text(encoding="utf-8").replace(
            "picked: false\n", "picked: true\npicked: false\n"
        ),
        encoding="utf-8",
    )

    assert main(["--vault", str(vault), "set", "--slugs", "web1,web2"]) == 0
    out = json.loads(capsys.readouterr().out)

    assert out["picked"] == ["web2"]
    assert out["missing"] == []
    assert [a["path"] for a in out["anomalies"]] == ["Sources/web1.md"]
    assert "picked" in out["anomalies"][0]["error"]
    fm = parse_frontmatter(note.read_text(encoding="utf-8"))
    assert fm["picked"] is False  # what the report now agrees with


@pytest.mark.parametrize("held", ['"picked": true', "picked : true"])
def test_set_does_not_give_a_note_a_second_picked_line(
    vault: Path, capsys: pytest.CaptureFixture[str], held: str
) -> None:
    # The note names `picked` in a shape the reader cannot decode. Inserting a plain
    # line beside it leaves the note naming the key twice, a fault of the pick's own
    # making that `kboat-validate` would then report.
    note = vault / "Sources" / "web2.md"
    before = note.read_text(encoding="utf-8").replace("picked: false\n", f"{held}\n")
    note.write_text(before, encoding="utf-8")

    assert main(["--vault", str(vault), "set", "--slugs", ""]) == 0
    out = json.loads(capsys.readouterr().out)

    assert [a["path"] for a in out["anomalies"]] == ["Sources/web2.md"]
    assert repeated_keys(note.read_text(encoding="utf-8")) == {}


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
