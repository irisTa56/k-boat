"""End-to-end tests for the `kboat-notebooks` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kboat.notebooks.__main__ import main


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Sources").mkdir()
    return tmp_path


def _source(
    vault: Path,
    slug: str,
    *,
    notebook: str | None = None,
    distilled_date: str | None = None,
    **flags: bool,
) -> None:
    f = {"reading": False, "distill": False, "dismiss": False, "blocked": False, **flags}
    lines = [
        "type: source",
        f"title: {slug} title",
        "source_type: web_page",
        f"url: https://example.com/{slug}",
        *(f"{k}: {str(v).lower()}" for k, v in f.items()),
    ]
    if distilled_date:
        lines.append(f"distilled_date: {distilled_date}")
    if notebook:
        lines.append(f"notebooklm_id: {notebook}")
    text = "---\n" + "\n".join(lines) + "\n---\n"
    (vault / "Sources" / f"{slug}.md").write_text(text, encoding="utf-8")


def _listing(tmp_path: Path, *notebooks: dict[str, object]) -> Path:
    path = tmp_path / "notebooks.json"
    payload = {"notebooks": list(notebooks), "count": len(notebooks)}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _nb(notebook: str, *, is_owner: bool = True) -> dict[str, object]:
    return {"id": notebook, "title": f"{notebook} title", "is_owner": is_owner}


def _run(
    vault: Path, capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, dict[str, object]]:
    code = main(["--vault", str(vault), *args])
    return code, json.loads(capsys.readouterr().out)


def test_sweep_set_is_the_opened_sources_nothing_else_watches(
    vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _source(vault, "opened", notebook="nb-opened", reading=True)
    _source(vault, "unopened", notebook="nb-unopened")
    _source(vault, "discarded", reading=True)
    _source(vault, "parked", notebook="nb-parked", reading=True, blocked=True)
    _source(vault, "dropped", notebook="nb-dropped", reading=True, dismiss=True)
    _source(vault, "ripe", notebook="nb-ripe", reading=True, distill=True)
    _source(
        vault,
        "distilled",
        notebook="nb-distilled",
        reading=True,
        distill=True,
        distilled_date="2026-10-01",
    )
    listing = _listing(
        tmp_path,
        *(_nb(f"nb-{s}") for s in ("opened", "unopened", "parked", "dropped", "ripe", "distilled")),
    )

    code, report = _run(vault, capsys, "sweep", "--notebooks", str(listing))

    assert code == 0
    assert report["sweep_set"] == [
        {
            "slug": "distilled",
            "title": "distilled title",
            "url": "https://example.com/distilled",
            "source_type": "web_page",
            "notebooklm_id": "nb-distilled",
        },
        {
            "slug": "opened",
            "title": "opened title",
            "url": "https://example.com/opened",
            "source_type": "web_page",
            "notebooklm_id": "nb-opened",
        },
    ]


def test_sweep_takes_an_id_the_listing_lacks_out_of_the_set_and_names_it(
    vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _source(vault, "sound", notebook="nb-sound", reading=True)
    _source(vault, "gone", notebook="nb-gone", reading=True)
    _source(vault, "unopened", notebook="nb-unopened")
    _source(vault, "ripe-gone", notebook="nb-ripe-gone", reading=True, distill=True)
    listing = _listing(tmp_path, _nb("nb-sound"), _nb("nb-unopened"))

    code, report = _run(vault, capsys, "sweep", "--notebooks", str(listing))

    assert code == 0
    sweep_set = report["sweep_set"]
    assert isinstance(sweep_set, list)
    assert [s["slug"] for s in sweep_set] == ["sound"]
    # Every stored id is read against the listing, not the set's alone: the count
    # is what tells one notebook gone from a listing fetched under another account.
    assert report["absent"] == [
        {"slug": "gone", "title": "gone title", "notebooklm_id": "nb-gone", "in_sweep_set": True},
        {
            "slug": "ripe-gone",
            "title": "ripe-gone title",
            "notebooklm_id": "nb-ripe-gone",
            "in_sweep_set": False,
        },
    ]
    assert report["counts"] == {"stored_ids": 4, "resolved_ids": 2, "listed_notebooks": 2}


def test_sweep_names_the_owned_notebooks_no_note_references(
    vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _source(vault, "sound", notebook="nb-sound", reading=True)
    listing = _listing(tmp_path, _nb("nb-sound"), _nb("nb-stray"), _nb("nb-shared", is_owner=False))

    _, report = _run(vault, capsys, "sweep", "--notebooks", str(listing))

    assert report["unreferenced"] == [{"id": "nb-stray", "title": "nb-stray title"}]


@pytest.mark.parametrize("stored", [["nb-elsewhere"], []], ids=["none resolves", "none stored"])
def test_sweep_makes_no_unreferenced_list_where_no_stored_id_resolves(
    stored: list[str], vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Under the wrong signed-in account every notebook listed is one no note names,
    # and a vault holding no id gives the listing nothing to be checked against.
    for i, notebook in enumerate(stored):
        _source(vault, f"s{i}", notebook=notebook, reading=True)
    listing = _listing(tmp_path, _nb("nb-theirs"), _nb("nb-theirs-too"))

    _, report = _run(vault, capsys, "sweep", "--notebooks", str(listing))

    assert report["unreferenced"] is None
    counts = report["counts"]
    assert isinstance(counts, dict)
    assert counts["resolved_ids"] == 0


def test_sweep_reports_a_note_it_could_not_read(
    vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _source(vault, "sound", notebook="nb-sound", reading=True)
    (vault / "Sources" / "torn.md").write_text("no frontmatter here\n", encoding="utf-8")
    listing = _listing(tmp_path, _nb("nb-sound"), _nb("nb-stray"))

    code, report = _run(vault, capsys, "sweep", "--notebooks", str(listing))

    # Still a report: the unread note may be the one carrying `nb-stray`.
    assert code == 0
    anomalies = report["anomalies"]
    assert isinstance(anomalies, list)
    assert [a["path"] for a in anomalies] == ["Sources/torn.md"]
    assert report["unreferenced"] == [{"id": "nb-stray", "title": "nb-stray title"}]


def test_sweep_exits_1_with_its_report_where_sources_could_not_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listing = _listing(tmp_path, _nb("nb-a"))

    code, report = _run(tmp_path, capsys, "sweep", "--notebooks", str(listing))

    assert code == 1
    anomalies = report["anomalies"]
    assert isinstance(anomalies, list)
    assert [a["path"] for a in anomalies] == ["Sources"]
    assert report["sweep_set"] == []
    assert report["unreferenced"] is None


@pytest.mark.parametrize(
    "content",
    ["", "{not json", "[]", '{"error": true, "code": "AUTH"}', '{"notebooks": [{"title": "x"}]}'],
    ids=["empty", "not json", "not an object", "an error object", "a notebook with no id"],
)
def test_a_file_that_is_not_a_listing_is_refused_rather_than_read_as_no_notebooks(
    content: str, vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A failed `notebooklm list … > file` leaves exactly these, and read as an
    # empty account each would report every stored id as a notebook that is gone.
    _source(vault, "sound", notebook="nb-sound", reading=True)
    path = tmp_path / "notebooks.json"
    path.write_text(content, encoding="utf-8")

    code = main(["--vault", str(vault), "sweep", "--notebooks", str(path)])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "not a notebook listing" in captured.err


def test_a_listing_file_that_is_not_there_is_refused(
    vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--vault", str(vault), "sweep", "--notebooks", str(tmp_path / "nope.json")])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "nope.json" in captured.err


def test_resolve_answers_each_id_against_the_listing_and_the_vaults_other_ids(
    vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _source(vault, "ripe", notebook="nb-ripe", reading=True, distill=True)
    _source(vault, "ripe-gone", notebook="nb-ripe-gone", distill=True)
    _source(vault, "other", notebook="nb-other")
    listing = _listing(tmp_path, _nb("nb-ripe"), _nb("nb-other"), _nb("nb-stray"))

    code, report = _run(
        vault,
        capsys,
        "resolve",
        "--notebooks",
        str(listing),
        "--id",
        "nb-ripe",
        "--id",
        "nb-ripe-gone",
    )

    assert code == 0
    assert report == {
        "ids": [
            {"notebooklm_id": "nb-ripe", "listed": True},
            {"notebooklm_id": "nb-ripe-gone", "listed": False},
        ],
        "counts": {"stored_ids": 3, "resolved_ids": 2, "listed_notebooks": 3},
        "anomalies": [],
    }


def test_resolve_exits_1_with_its_report_where_sources_could_not_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listing = _listing(tmp_path, _nb("nb-a"))

    code, report = _run(tmp_path, capsys, "resolve", "--notebooks", str(listing), "--id", "nb-a")

    assert code == 1
    assert report["ids"] == [{"notebooklm_id": "nb-a", "listed": True}]
    counts = report["counts"]
    assert isinstance(counts, dict)
    assert counts["stored_ids"] == 0
    anomalies = report["anomalies"]
    assert isinstance(anomalies, list)
    assert [a["path"] for a in anomalies] == ["Sources"]
