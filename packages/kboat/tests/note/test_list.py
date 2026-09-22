"""Tests for `kboat-note list` — a note folder's frontmatter, read for a skill."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kboat.lock import vault_lock
from kboat.note.__main__ import main


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for sub in ("Sources", "Kindles", "Repos", "Feeds"):
        (tmp_path / sub).mkdir()
    return tmp_path


def _note(vault: Path, slug: str, **fields: str) -> None:
    lines = "".join(f"{k}: {v}\n" for k, v in fields.items())
    (vault / "Sources" / f"{slug}.md").write_text(f"---\n{lines}---\n", encoding="utf-8")


def _list(
    vault: Path, capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, dict[str, object]]:
    code = main(["list", "--type", "source", "--vault", str(vault), *args])
    return code, json.loads(capsys.readouterr().out)


def _slugs(report: dict[str, object]) -> list[str]:
    notes = report["notes"]
    assert isinstance(notes, list)
    return [n["slug"] for n in notes]


def _anomaly_paths(report: dict[str, object]) -> list[str]:
    anomalies = report["anomalies"]
    assert isinstance(anomalies, list)
    return [a["path"] for a in anomalies]


def test_lists_every_note_with_its_frontmatter(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _note(vault, "aaa", title="A", blocked="false")
    _note(vault, "bbb", title="B", blocked="true")

    code, report = _list(vault, capsys)

    assert code == 0
    assert report == {
        "notes": [
            {
                "slug": "aaa",
                "path": "Sources/aaa.md",
                "frontmatter": {"title": "A", "blocked": False},
            },
            {
                "slug": "bbb",
                "path": "Sources/bbb.md",
                "frontmatter": {"title": "B", "blocked": True},
            },
        ],
        "anomalies": [],
        "counts": {"notes": 2, "anomalies": 0},
    }


def test_an_evicted_note_is_an_anomaly_not_a_missing_note(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A glob skips the placeholder, so without the entry the evicted note would
    # simply not be in the answer and nothing would say so.
    _note(vault, "aaa", title="A")
    (vault / "Sources" / ".bbb.md.icloud").write_bytes(b"")

    code, report = _list(vault, capsys)

    assert code == 0
    assert _slugs(report) == ["aaa"]
    assert _anomaly_paths(report) == ["Sources/.bbb.md.icloud"]


def test_a_note_that_does_not_parse_is_reported_and_the_rest_still_listed(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _note(vault, "aaa", title="A")
    (vault / "Sources" / "bad.md").write_text("no frontmatter here\n", encoding="utf-8")
    (vault / "Sources" / "latin1.md").write_bytes(b"---\ntitle: caf\xe9\n---\n")

    code, report = _list(vault, capsys)

    assert code == 0
    assert _slugs(report) == ["aaa"]
    assert _anomaly_paths(report) == ["Sources/bad.md", "Sources/latin1.md"]
    assert report["counts"] == {"notes": 1, "anomalies": 2}


@pytest.mark.parametrize(
    ("break_folder", "lead"),
    [
        (lambda s: s.rmdir(), "absent"),
        (lambda s: (s.rmdir(), s.write_text("x\n", encoding="utf-8")), "not a directory"),
        (lambda s: s.chmod(0o000), "refused"),
    ],
    ids=["absent", "not-a-directory", "refused"],
)
def test_a_folder_it_cannot_read_exits_1_with_the_report(
    vault: Path, capsys: pytest.CaptureFixture[str], break_folder, lead: str
) -> None:
    # An unlistable folder reads as an empty one to a glob; the answer drawn from
    # it would be "no such note" or "nothing in the DLQ".
    sources = vault / "Sources"
    break_folder(sources)
    try:
        code, report = _list(vault, capsys)
    finally:
        if sources.is_dir():
            sources.chmod(0o755)

    assert code == 1
    assert report["notes"] == []
    anomalies = report["anomalies"]
    assert isinstance(anomalies, list)
    assert [a["path"] for a in anomalies] == ["Sources"]
    assert anomalies[0]["error"].startswith(lead)


def test_the_slug_answers_present_evicted_and_absent_apart(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _note(vault, "here", title="H")
    _note(vault, "other", title="O")
    (vault / "Sources" / ".gone.md.icloud").write_bytes(b"")
    (vault / "Sources" / ".elsewhere.md.icloud").write_bytes(b"")

    code, report = _list(vault, capsys, "--slug", "here")
    assert code == 0
    assert _slugs(report) == ["here"]
    assert report["anomalies"] == []

    # Evicted: nothing at the name, a placeholder beside it — and only its own.
    code, report = _list(vault, capsys, "--slug", "gone")
    assert code == 0
    assert report["notes"] == []
    assert _anomaly_paths(report) == ["Sources/.gone.md.icloud"]

    # Absent: neither.
    code, report = _list(vault, capsys, "--slug", "nothing")
    assert code == 0
    assert report == {"notes": [], "anomalies": [], "counts": {"notes": 0, "anomalies": 0}}


def test_a_stale_stub_beside_its_note_does_not_make_the_note_evicted(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # File before placeholder: the note is here, so the stub is not an eviction.
    _note(vault, "here", title="H")
    (vault / "Sources" / ".here.md.icloud").write_bytes(b"")

    code, report = _list(vault, capsys, "--slug", "here")

    assert code == 0
    assert _slugs(report) == ["here"]
    assert report["anomalies"] == []


def test_a_non_file_at_the_slug_is_reported_rather_than_read_as_evicted(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A directory holds the name and a stale stub sits beside it: the stub must
    # not answer for it, since no download frees a name a directory holds.
    (vault / "Sources" / "held.md").mkdir()
    (vault / "Sources" / ".held.md.icloud").write_bytes(b"")

    code, report = _list(vault, capsys, "--slug", "held")

    assert code == 0
    assert report["notes"] == []
    assert _anomaly_paths(report) == ["Sources/held.md"]


def test_a_slug_in_an_unreadable_folder_is_not_answered_absent(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (vault / "Sources").rmdir()

    code, report = _list(vault, capsys, "--slug", "any")

    assert code == 1
    assert _anomaly_paths(report) == ["Sources"]


def test_field_cuts_the_frontmatter_and_flagged_keeps_the_true_ones(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _note(vault, "dlq", title="D", url="https://d", blocked="true")
    _note(vault, "fine", title="F", url="https://f", blocked="false")
    _note(vault, "unset", title="U")

    code, report = _list(vault, capsys, "--flagged", "blocked", "--field", "title")

    assert code == 0
    assert report["notes"] == [
        {"slug": "dlq", "path": "Sources/dlq.md", "frontmatter": {"title": "D"}}
    ]


def test_a_filter_does_not_hide_a_note_it_could_not_read(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing shows an unreadable note to fail the filter, so it stays reported.
    (vault / "Sources" / "bad.md").write_text("no fence\n", encoding="utf-8")
    (vault / "Sources" / ".evicted.md.icloud").write_bytes(b"")

    code, report = _list(vault, capsys, "--flagged", "blocked")

    assert code == 0
    assert report["notes"] == []
    assert _anomaly_paths(report) == ["Sources/.evicted.md.icloud", "Sources/bad.md"]


def _block_scalar_note(vault: Path, slug: str, *, blocked: str = "false") -> None:
    (vault / "Sources" / f"{slug}.md").write_text(
        f"---\ntitle: T\nsummary: |\n  line one\n  line two\nblocked: {blocked}\n---\n",
        encoding="utf-8",
    )


def test_a_field_the_reader_does_not_model_is_reported_not_dropped(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The note is listed, but without the entry its `summary` would read as one
    # the note does not have.
    _block_scalar_note(vault, "blk")

    code, report = _list(vault, capsys)

    assert code == 0
    notes = report["notes"]
    assert isinstance(notes, list)
    assert [n["slug"] for n in notes] == ["blk"]
    assert "summary" not in notes[0]["frontmatter"]
    anomalies = report["anomalies"]
    assert isinstance(anomalies, list)
    assert [a["path"] for a in anomalies] == ["Sources/blk.md"]
    assert "summary" in anomalies[0]["error"]


def test_an_unmodelled_field_is_reported_only_where_the_answer_would_show_it(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _block_scalar_note(vault, "blk")
    _block_scalar_note(vault, "dlq", blocked="true")

    # Not asked for: nothing is hidden.
    code, report = _list(vault, capsys, "--field", "title")
    assert code == 0
    assert report["anomalies"] == []

    # Asked for, but on a note the filter drops: nothing is hidden either.
    code, report = _list(vault, capsys, "--flagged", "blocked", "--field", "summary")
    assert _slugs(report) == ["dlq"]
    assert _anomaly_paths(report) == ["Sources/dlq.md"]


def test_an_unmodelled_flag_is_reported_whatever_the_filter(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing shows the note to fail the filter, so it may be one it keeps.
    (vault / "Sources" / "odd.md").write_text(
        "---\ntitle: T\nblocked:\n  nested: true\n---\n", encoding="utf-8"
    )

    code, report = _list(vault, capsys, "--flagged", "blocked", "--field", "title")

    assert code == 0
    assert report["notes"] == []
    assert _anomaly_paths(report) == ["Sources/odd.md"]


@pytest.mark.parametrize(
    "args",
    [
        ["--field", "no_such_field"],
        ["--flagged", "no_such_field"],
        ["--flagged", "title"],
    ],
)
def test_a_name_outside_the_schema_is_a_usage_error(vault: Path, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["list", "--type", "source", "--vault", str(vault), *args])
    assert exit_info.value.code == 2


def test_reads_the_folder_of_the_type_it_is_given(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (vault / "Repos" / "r.md").write_text("---\ntitle: o/r\n---\n", encoding="utf-8")
    _note(vault, "s", title="S")

    code = main(["list", "--type", "repo", "--vault", str(vault)])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert _slugs(report) == ["r"]
    assert report["notes"][0]["path"] == "Repos/r.md"


def test_it_reads_a_vault_another_run_holds(
    vault: Path, capsys: pytest.CaptureFixture[str], brief_lock_wait: None
) -> None:
    _note(vault, "aaa", title="A")
    with vault_lock(vault):
        code, report = _list(vault, capsys)
    assert code == 0
    assert _slugs(report) == ["aaa"]
