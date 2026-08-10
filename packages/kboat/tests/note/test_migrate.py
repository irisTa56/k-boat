"""Tests for `kboat-note migrate-slugs` — the one-off move to canonical slugs."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

import kboat.note.migrate as migrate_mod
from kboat.naming import note_slug, url_slug
from kboat.note.migrate import migrate, plan
from kboat.schema import FEED, REPO, SOURCE
from kboat.write import build_note

# The pre-migration recipe: the stored URL hashed verbatim. A URL whose canonical
# form differs from itself is therefore filed under a slug the oracle disowns.
STALE_URL = "https://example.com/post/"
STALE = url_slug(STALE_URL)
FRESH = note_slug(STALE_URL)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for sub in ("Sources", "Kindles", "Repos", "Feeds", "PDFs"):
        (tmp_path / sub).mkdir()
    return tmp_path


def _write(vault: Path, folder: str, slug: str, fields: dict[str, object]) -> Path:
    """Put a note on disk at exactly `slug`, whatever the oracle would say.

    Deliberately not through `upsert`: that is the writer this migration exists
    to satisfy, so it refuses the very notes the fixture has to create.
    """
    schema = {"Sources": SOURCE, "Repos": REPO, "Feeds": FEED}[folder]
    path = vault / folder / f"{slug}.md"
    path.write_text(build_note(schema, fields), encoding="utf-8")
    return path


def _source(vault: Path, slug: str, url: str, **extra: object) -> Path:
    return _write(
        vault,
        "Sources",
        slug,
        {
            "type": "source",
            "title": "T",
            "source_type": "web_page",
            "url": url,
            "added_date": "2026-07-01",
            **extra,
        },
    )


def test_a_canonical_vault_reports_nothing(vault: Path) -> None:
    _source(vault, FRESH, STALE_URL)
    _write(
        vault,
        "Feeds",
        note_slug("https://f.test/a"),
        {"type": "feed", "title": "F", "url": "https://f.test/a"},
    )

    report = migrate(vault, apply=False)

    assert report.rows == [] and report.skipped == []
    assert report.counts()["mismatched"] == 0


def test_dry_run_reports_the_stale_name_and_moves_nothing(vault: Path) -> None:
    path = _source(vault, STALE, STALE_URL)

    report = migrate(vault, apply=False)

    assert [r.to_json() for r in report.rows] == [
        {
            "type": "source",
            "path": f"Sources/{STALE}.md",
            "current": STALE,
            "expected": FRESH,
            "url": STALE_URL,
            "moves": [],
            "status": "pending",
        }
    ]
    assert path.exists() and not (vault / "Sources" / f"{FRESH}.md").exists()


def test_apply_renames_every_url_named_type(vault: Path) -> None:
    repo_url = "https://github.com/o/r/"
    feed_url = "https://f.test/a/"
    _source(vault, STALE, STALE_URL)
    _write(
        vault,
        "Repos",
        url_slug(repo_url),
        {"type": "repo", "title": "o/r", "url": repo_url, "role": "library"},
    )
    _write(vault, "Feeds", url_slug(feed_url), {"type": "feed", "title": "F", "url": feed_url})

    report = migrate(vault, apply=True)

    assert report.counts()["renamed"] == 3
    assert {r.status for r in report.rows} == {"renamed"}
    assert (vault / "Sources" / f"{FRESH}.md").exists()
    assert (vault / "Repos" / f"{note_slug(repo_url)}.md").exists()
    assert (vault / "Feeds" / f"{note_slug(feed_url)}.md").exists()
    # And a second pass has nothing left to say.
    assert migrate(vault, apply=True).rows == []


def test_a_kindle_note_is_never_scanned(vault: Path) -> None:
    # Its slug is an ASIN — an id, not a hash of anything, so there is nothing to
    # recompute and nothing to disagree with.
    (vault / "Kindles" / "B00TEST.md").write_text("---\ntype: kindle\n---\n", encoding="utf-8")

    assert migrate(vault, apply=True).counts() == {
        "mismatched": 0,
        "renamed": 0,
        "conflicts": 0,
        "failed": 0,
        "skipped": 0,
        "unreadable_dirs": 0,
    }
    assert (vault / "Kindles" / "B00TEST.md").exists()


def test_a_note_already_at_the_target_name_is_a_conflict_never_an_overwrite(vault: Path) -> None:
    stale = _source(vault, STALE, STALE_URL)
    fresh = _source(vault, FRESH, STALE_URL, title="the one that is already there")

    report = migrate(vault, apply=True)

    assert [(r.status, r.current) for r in report.rows] == [("conflict", STALE)]
    assert report.unresolved == 1
    assert stale.exists(), "the stale note is left for a human, not deleted"
    assert "already there" in fresh.read_text(), "and the note in the way is untouched"


def test_a_note_directory_that_cannot_be_listed_is_skipped_not_scanned_clean(vault: Path) -> None:
    # "Nothing to do" is terminal for a repair that runs once, and this report is
    # what the `--apply` is approved from — so an unread directory has to say so
    # rather than come back as a vault that is already canonical.
    _source(vault, STALE, STALE_URL)
    (vault / "Sources").chmod(0o111)
    try:
        rows, skipped = plan(vault)
        counts = migrate(vault, apply=False).counts()
    finally:
        (vault / "Sources").chmod(0o755)

    assert rows == []
    assert [(s.path, s.reason.split(":")[0]) for s in skipped] == [("Sources", "unreadable_dir")]
    # Counted apart from the notes: one entry stands for however many went unseen,
    # so a `skipped: 1` would put a fixed number where the true one is unknown.
    assert (counts["skipped"], counts["unreadable_dirs"]) == (0, 1)


def test_an_evicted_pdf_is_reported_against_the_name_that_holds_the_placeholder(
    vault: Path,
) -> None:
    # Naming the other one sends a human to open a file that is present and
    # readable, with nothing in the report pointing at the blocker.
    _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.4\n")
    (vault / "PDFs" / f".{FRESH}.pdf.icloud").write_bytes(b"")

    rows, _ = plan(vault)

    row = next(r for r in rows if r.current == STALE)
    assert row.status == "conflict"
    assert row.detail == f"PDFs/{FRESH}.pdf is an iCloud placeholder"
    assert row.moves == [f"PDFs/{STALE}.pdf"]  # the source is there and travels


def test_a_pdf_already_across_and_then_evicted_is_not_a_conflict(vault: Path) -> None:
    # The half-applied case: a crash between the two renames leaves the PDF at the
    # target, where iCloud later evicts it. The pair is one rename from done and
    # nothing is overwritten, so refusing the row would strand it for good.
    _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f".{FRESH}.pdf.icloud").write_bytes(b"")

    rows, _ = plan(vault)

    row = next(r for r in rows if r.current == STALE)
    assert row.status == "pending"
    assert row.detail == ""
    assert row.moves == [], "nothing is at the old name to travel"


def test_a_stale_placeholder_beside_a_live_pdf_does_not_speak_for_it(vault: Path) -> None:
    # The two can coexist. Asking the placeholder question of both names first
    # turned a pair that migrates cleanly into a permanent conflict, reported
    # against a file anyone can open.
    _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.4\n")
    (vault / "PDFs" / f".{STALE}.pdf.icloud").write_bytes(b"")

    rows, _ = plan(vault)

    row = next(r for r in rows if r.current == STALE)
    assert row.status == "pending"
    assert row.moves == [f"PDFs/{STALE}.pdf"]
    # The move takes the file out from under the stub, which stays: the row says so
    # rather than reading as a clean rename, since no later pass revisits this note.
    assert f"PDFs/.{STALE}.pdf.icloud stays behind" in row.detail


def test_a_rename_names_the_notes_own_stub_it_strands(vault: Path) -> None:
    # Worse than the PDF's: a lone note placeholder fails `icloud_notes`, so from
    # the next day the whole routine stops — out of a report that never mentioned
    # it. The reporting side skips a stub beside a present file, so the side that
    # breaks the pair is the only one that can say so.
    _source(vault, STALE, STALE_URL)
    (vault / "Sources" / f".{STALE}.md.icloud").write_bytes(b"")

    report = migrate(vault, apply=True)

    row = next(r for r in report.rows if r.current == STALE)
    assert row.status == "renamed"
    assert f"Sources/.{STALE}.md.icloud stays behind" in row.detail
    assert (vault / "Sources" / f".{STALE}.md.icloud").exists(), "left, not deleted"


def test_the_stranded_stub_is_still_named_after_the_rename_lands(vault: Path) -> None:

    # The message is worth nothing on the dry run alone: it exists for the report a
    # completed `--apply` leaves, since the note's slug then matches and no later
    # pass revisits it. `migrate` sets `renamed` without touching `detail`, which is
    # incidental unless something holds it there.
    _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.4\n")
    (vault / "PDFs" / f".{STALE}.pdf.icloud").write_bytes(b"")

    report = migrate(vault, apply=True)

    row = next(r for r in report.rows if r.current == STALE)
    assert row.status == "renamed"
    assert f"PDFs/.{STALE}.pdf.icloud stays behind" in row.detail
    assert (vault / "PDFs" / f"{FRESH}.pdf").exists()
    assert (vault / "PDFs" / f".{STALE}.pdf.icloud").exists(), "the stub is left, not deleted"


def _raise(exc: BaseException):
    """A stand-in that fails the way the real call would, wherever it is patched."""

    def fail(*_args: object, **_kwargs: object) -> None:
        raise exc

    return fail


def test_a_failed_apply_drops_the_strand_it_never_vacated(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `plan` names the strand before the move. An apply that fails before any
    # rename vacated nothing, and the spec's condition is an apply that *vacates*
    # a name — so the line has to go, or the report sends a human to delete a
    # stub still paired with its own present file.
    stale = _source(vault, STALE, STALE_URL)
    stub = vault / "Sources" / f".{STALE}.md.icloud"
    stub.write_bytes(b"")
    monkeypatch.setattr(migrate_mod.os, "replace", _raise(PermissionError(13, "rename refused")))

    report = migrate(vault, apply=True)

    row = next(r for r in report.rows if r.current == STALE)
    assert row.status == "failed"
    assert "stays behind" not in row.detail
    assert "rename refused" in row.detail
    assert stale.exists() and stub.exists()


def test_a_failed_apply_keeps_the_strand_whose_rename_did_land(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other arm, and why the line is re-judged rather than dropped: `fsync_dir`
    # raises *after* its rename landed, so the stub really is lone now — and it is
    # a lone note placeholder that fails `icloud_notes` and stops the routine.
    _source(vault, STALE, STALE_URL)
    (vault / "Sources" / f".{STALE}.md.icloud").write_bytes(b"")
    monkeypatch.setattr(migrate_mod, "fsync_dir", _raise(OSError(5, "flush failed")))

    report = migrate(vault, apply=True)

    row = next(r for r in report.rows if r.current == STALE)
    assert row.status == "failed"
    assert f"Sources/.{STALE}.md.icloud stays behind" in row.detail
    assert "flush failed" in row.detail


def test_an_evicted_note_at_the_target_is_not_reported_as_taken(vault: Path) -> None:
    # Byte-identical rows for opposite remedies is the defect: a file there is a
    # human's to merge, while a placeholder is a file that has to come back — and
    # cannot be merged with, or even opened, until it does.
    _source(vault, STALE, STALE_URL)
    (vault / "Sources" / f".{FRESH}.md.icloud").write_bytes(b"")

    rows, _ = plan(vault)

    row = next(r for r in rows if r.current == STALE)
    assert row.status == "conflict"
    assert "is an iCloud placeholder" in row.detail
    assert "is taken" not in row.detail


def test_a_broken_symlink_with_a_stale_stub_beside_it_is_not_called_a_placeholder(
    vault: Path,
) -> None:
    # The name itself is asked before the placeholder beside it. "is an iCloud
    # placeholder" is the wording whose remedy is to wait for a download, and no
    # download frees a symlink, so the row would be re-reported every run with
    # nobody ever sent to clear it.
    _source(vault, STALE, STALE_URL)
    (vault / "Sources" / f"{FRESH}.md").symlink_to(vault / "Sources" / "nowhere.md")
    (vault / "Sources" / f".{FRESH}.md.icloud").write_bytes(b"")

    rows, _ = plan(vault)

    row = next(r for r in rows if r.current == STALE)
    assert row.status == "conflict"
    assert "is a name held by something else" in row.detail
    assert "placeholder" not in row.detail


def test_a_target_name_held_by_a_broken_symlink_is_not_a_merge(vault: Path) -> None:
    # `name_taken` answers yes for a dangling symlink, so without its own wording
    # this falls to "is taken" — which the spec routes to a human merging two
    # notes, and there is no second note. Nothing frees the name on its own.
    stale = _source(vault, STALE, STALE_URL)
    (vault / "Sources" / f"{FRESH}.md").symlink_to(vault / "Sources" / "nowhere.md")

    report = migrate(vault, apply=True)

    row = next(r for r in report.rows if r.current == STALE)
    assert row.status == "conflict"
    assert "is a name held by something else" in row.detail
    assert "is taken" not in row.detail
    assert stale.exists(), "nothing was renamed onto it"


def test_a_stub_probe_that_cannot_answer_does_not_refuse_the_rename(vault: Path) -> None:
    # No permission games needed: a filename of 248 bytes or more makes its
    # `.icloud` sibling exceed 255, so the probe cannot ask. Those are exactly the
    # long title-derived names this repair exists for, and the rename is what makes
    # the name probeable again — so "could not tell" is a report, not a refusal.
    long_slug = "b" * 248
    long_note = _source(vault, long_slug, "https://example.com/long-one/")

    report = migrate(vault, apply=True)

    row = next(r for r in report.rows if r.current == long_slug)
    assert row.status == "renamed", "the note this repair exists for still moves"
    assert "could not be determined" in row.detail
    assert not long_note.exists()
    assert (vault / "Sources" / f"{note_slug('https://example.com/long-one/')}.md").exists()


def test_a_failed_apply_keeps_the_strand_account_it_was_given(vault: Path, monkeypatch) -> None:
    # `fsync_dir` raises *after* its rename has landed, so this row is one whose
    # move happened — the strand is real, nothing is at the old name for a later
    # pass to find, and replacing the detail loses the only account of a lone
    # placeholder that stops the routine from the next day on.
    _source(vault, STALE, STALE_URL)
    (vault / "Sources" / f".{STALE}.md.icloud").write_bytes(b"")

    def refuse(_directory: Path) -> None:
        raise OSError("no dir flush")

    monkeypatch.setattr(migrate_mod, "fsync_dir", refuse)
    report = migrate(vault, apply=True)

    row = next(r for r in report.rows if r.current == STALE)
    assert row.status == "failed"
    assert f"Sources/.{STALE}.md.icloud stays behind" in row.detail
    assert "no dir flush" in row.detail
    assert not (vault / "Sources" / f"{STALE}.md").exists(), "the rename landed"


def test_a_target_name_the_vault_refuses_to_read_is_this_row_s_conflict(
    vault: Path, monkeypatch
) -> None:
    # A refusal is not an answer, so the row must not move; and it is this row's
    # problem, not the pass's — one unreadable name cannot cost the report every
    # other note's row, which is what the `--apply` is approved from.
    stale = _source(vault, STALE, STALE_URL)
    other = _source(vault, "0000ffff1111", "https://example.com/other-page")
    real_lstat = Path.lstat

    def refusing_lstat(self: Path, **kwargs: object) -> os.stat_result:
        if self.stem == FRESH:
            raise PermissionError("Operation not permitted")
        return real_lstat(self, **kwargs)

    monkeypatch.setattr(Path, "lstat", refusing_lstat)

    report = migrate(vault, apply=True)

    row = next(r for r in report.rows if r.current == STALE)
    assert row.status == "conflict"
    assert "could not be read" in row.detail
    assert "is taken" not in row.detail, "the remedy is the vault, not a human merge"
    assert stale.exists(), "nothing was renamed onto a name that could not be read"
    # The rest of the scan still ran: the other note got its row and its rename.
    assert not other.exists()
    assert [r.status for r in report.rows if r.current != STALE] == ["renamed"]


def test_two_notes_wanting_one_slug_collide_in_the_dry_run_too(vault: Path) -> None:
    # Two links to one page, neither of them canonical, so both want the slug the
    # page names — and a dry run that did not say so would not predict the apply.
    _source(vault, url_slug("https://c.test/a/"), "https://c.test/a/")
    _source(vault, url_slug("https://c.test/a?utm_source=x"), "https://c.test/a?utm_source=x")

    statuses = [r.status for r in plan(vault)[0]]

    assert sorted(statuses) == ["conflict", "pending"]


def test_a_source_with_no_url_is_skipped_not_moved(vault: Path) -> None:
    # An uploaded PDF carries no `url`, so its slug answers to nothing.
    path = _source(vault, "handmade", "")

    report = migrate(vault, apply=True)

    assert [s.to_json() for s in report.skipped] == [
        {"path": "Sources/handmade.md", "reason": "no_url"}
    ]
    assert report.rows == [] and path.exists()


@pytest.mark.parametrize(
    "held",
    [
        "url:\n  - https://example.com/post/",
        "url: >-\n  https://example.com/post/",
        '"url": https://example.com/post/',
        "url : https://example.com/post/",
    ],
    ids=["a one-item list", "a folded scalar", "a quoted key", "a space before the colon"],
)
def test_a_url_the_reader_cannot_read_is_reported_as_such_not_as_absent(
    vault: Path, held: str
) -> None:
    # `no_url` is what an upload source looks like, and the operator is told to
    # pass over it — so a note whose url is merely unreadable must not land
    # there, or its stale name goes unreported and a later capture for the same
    # page writes a second note.
    path = vault / "Sources" / "unreadable.md"
    path.write_text(f"---\ntype: source\ntitle: T\n{held}\n---\n", encoding="utf-8")

    report = migrate(vault, apply=True)

    assert [s.reason for s in report.skipped] == ["unreadable_url"]
    assert path.exists()


def test_a_source_whose_url_is_empty_is_the_upload_case(vault: Path) -> None:
    # The other side of the same split: a bare `url:` is an upload source, which
    # is where it belongs and is not a finding.
    (vault / "Sources" / "upload.md").write_text(
        "---\ntype: source\ntitle: T\nurl:\n---\n", encoding="utf-8"
    )

    assert [s.reason for s in migrate(vault, apply=True).skipped] == ["no_url"]


@pytest.mark.parametrize(
    "held",
    [
        "reading_link:\n  - '[[{slug}.pdf]]'",
        "reading_link: >-\n  [[{slug}.pdf]]",
        "reading_link: |-\n  [[{slug}.pdf]]",
        '"reading_link": [[{slug}.pdf]]',
        "reading_link : [[{slug}.pdf]]",
    ],
    ids=[
        "a one-item list",
        "a folded scalar",
        "a literal scalar",
        "a quoted key",
        "a space before the colon",
    ],
)
def test_a_reading_link_the_reader_cannot_read_still_holds_the_pair(vault: Path, held: str) -> None:
    # Every shape the reader hands back as something other than the link's text —
    # a list (what Obsidian writes for a property typed List), a block scalar, a
    # key outside the grammar. Each still names the note's PDF and none can be
    # rewritten, so the pair must not move around them: reading any of these as
    # "there is no link" is what would dangle it.
    (vault / "Sources" / f"{STALE}.md").write_text(
        f"---\ntype: source\ntitle: T\nsource_type: pdf\nurl: {STALE_URL}\n"
        f"{held.format(slug=STALE)}\n---\n",
        encoding="utf-8",
    )
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.7\n")

    report = migrate(vault, apply=True)

    assert report.rows[0].status == "conflict"
    assert "cannot be retargeted" in report.rows[0].detail
    assert (vault / "Sources" / f"{STALE}.md").exists()
    assert (vault / "PDFs" / f"{STALE}.pdf").exists()


def test_a_url_no_parser_can_take_costs_its_own_note_and_not_the_pass(vault: Path) -> None:
    # This runs once over a whole vault, so a note holding a string the URL
    # parser rejects must not abort the scan before the notes after it are seen.
    _source(vault, "unparseable", "https://example.com:99999/x")
    _source(vault, STALE, STALE_URL)

    report = migrate(vault, apply=True)

    assert [s.path for s in report.skipped] == ["Sources/unparseable.md"]
    assert report.skipped[0].reason.startswith("unusable_url")
    assert [r.status for r in report.rows] == ["renamed"]
    assert (vault / "Sources" / f"{FRESH}.md").exists()


def test_one_page_may_hold_a_note_in_two_folders(vault: Path) -> None:
    # Every type now hashes the same canonical URL, so a page kept as a feed and
    # later ingested as a source lands on one slug in two folders. The slug names
    # a file inside a folder, so that is not a clash and neither note may be held
    # back for it.
    _source(vault, STALE, STALE_URL)
    _write(vault, "Feeds", url_slug(STALE_URL), {"type": "feed", "title": "F", "url": STALE_URL})

    report = migrate(vault, apply=True)

    assert [r.status for r in report.rows] == ["renamed", "renamed"]
    assert (vault / "Sources" / f"{FRESH}.md").exists()
    assert (vault / "Feeds" / f"{FRESH}.md").exists()


def test_a_note_whose_frontmatter_does_not_parse_is_skipped(vault: Path) -> None:
    (vault / "Sources" / "broken.md").write_text("no frontmatter here\n", encoding="utf-8")

    report = migrate(vault, apply=True)

    assert len(report.skipped) == 1
    assert report.skipped[0].reason.startswith("parse_error")


def test_a_pdf_pair_is_still_paired_after_a_power_loss(vault: Path) -> None:
    # Each rename's directory entry is flushed before the next, so the pair
    # cannot come back with the note moved and its file left behind — which the
    # scan would never look at again, the note being canonical by then.
    flushed: list[str] = []
    original = migrate_mod.fsync_dir

    def record(directory: Path) -> None:
        flushed.append(directory.name)
        original(directory)

    _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.7\n")

    with mock.patch.object(migrate_mod, "fsync_dir", record):
        migrate(vault, apply=True)

    assert flushed == ["PDFs", "Sources"], "the file's move is made durable before the note's"


def test_a_pdf_source_moves_with_its_file_and_its_reading_link(vault: Path) -> None:
    _source(
        vault,
        STALE,
        STALE_URL,
        source_type="pdf",
        reading_link=f"[[{STALE}.pdf]]",
    )
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.7\n")

    report = migrate(vault, apply=True)

    assert report.rows[0].moves == [f"PDFs/{STALE}.pdf"]
    assert not (vault / "PDFs" / f"{STALE}.pdf").exists()
    assert (vault / "PDFs" / f"{FRESH}.pdf").read_bytes() == b"%PDF-1.7\n"
    assert f"[[{FRESH}.pdf]]" in (vault / "Sources" / f"{FRESH}.md").read_text()


@pytest.mark.parametrize(
    "shape",
    [
        "[[{slug}.pdf]]",
        "[[{slug}.pdf#page=7]]",
        "[[{slug}.pdf#^highlight-abc]]",
        "[[{slug}.pdf|the paper]]",
        "![[{slug}.pdf]]",
        "![[PDFs/{slug}.pdf#page=7]]",
        "[[PDFs/{slug}.pdf]]",
    ],
    ids=[
        "a bare link",
        "a PDF++ page link",
        "a highlight link",
        "a display alias",
        "an embed",
        "a folder-qualified embed with a page",
        "a folder-qualified link",
    ],
)
def test_a_reading_link_keeps_everything_but_the_filename(vault: Path, shape: str) -> None:
    # `kboat-notes` has the reader upgrading this link as they read — a PDF++
    # page or highlight, or an alias — and Obsidian writes the link itself as an
    # embed or folder-qualified depending on its settings. Rewriting the whole
    # value would cost the reader the place they had got to; leaving it alone
    # would dangle the link.
    _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=shape.format(slug=STALE))
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.7\n")

    report = migrate(vault, apply=True)

    assert [r.status for r in report.rows] == ["renamed"]
    text = (vault / "Sources" / f"{FRESH}.md").read_text()
    assert shape.format(slug=FRESH) in text
    assert STALE not in text


def test_a_reading_link_naming_the_pdf_in_an_unreadable_shape_holds_the_pair(
    vault: Path,
) -> None:
    # A link this cannot rewrite must not look like a note with no link at all:
    # moving the pair would leave it pointing at a name nothing occupies, and the
    # reader would find out by clicking it. Reported like any other way the pair
    # cannot move, for a hand-edit and a re-run.
    note = _source(
        vault,
        STALE,
        STALE_URL,
        source_type="pdf",
        reading_link=f"see [[{STALE}.pdf]] and its notes",
    )
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.7\n")

    report = migrate(vault, apply=True)

    assert report.rows[0].status == "conflict"
    assert "cannot be retargeted" in report.rows[0].detail
    assert note.exists() and (vault / "PDFs" / f"{STALE}.pdf").exists()


@pytest.mark.parametrize("in_the_way", ["note", "pdf"])
def test_a_conflict_row_says_what_the_conflict_would_have_moved(
    vault: Path, in_the_way: str
) -> None:
    # A reader triaging the dry run decides from `moves` what a conflict costs;
    # an empty one on a source that has a PDF reads as a note with nothing to
    # strand. True of every conflict kind, the PDF's own included.
    _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.7\n")
    if in_the_way == "note":
        _source(vault, FRESH, STALE_URL, title="the one in the way")
    else:
        (vault / "PDFs" / f"{FRESH}.pdf").write_bytes(b"%PDF-someone else's\n")

    report = migrate(vault, apply=True)

    assert report.rows[0].status == "conflict"
    assert report.rows[0].moves == [f"PDFs/{STALE}.pdf"]


def test_an_evicted_pdf_holds_the_pair_rather_than_reading_as_no_pdf(vault: Path) -> None:
    # The vault is iCloud-synced, so an evicted PDF is a `.<slug>.pdf.icloud`
    # placeholder and `exists()` says `False` for it exactly as it would for a
    # web page that never had one. Moved past, the file arrives later under the
    # old name with the note and its link already pointing elsewhere.
    note = _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f".{STALE}.pdf.icloud").write_bytes(b"")

    report = migrate(vault, apply=True)

    assert report.rows[0].status == "conflict"
    assert "iCloud placeholder" in report.rows[0].detail
    assert report.rows[0].moves == [f"PDFs/{STALE}.pdf"]
    assert note.exists()
    assert f"[[{STALE}.pdf]]" in note.read_text(), "and the link still names the file coming back"


@pytest.mark.parametrize("evicted", ["note", "pdf"])
def test_a_target_name_held_by_an_evicted_file_is_taken(vault: Path, evicted: str) -> None:
    # The target's name is spoken for by a file that is not gone, only not here
    # yet. Renaming onto it claims an identity another note still holds, and
    # iCloud settles that later by suffixing or dropping one — a duplicate for
    # one page arriving quietly, which is what this whole change is against.
    note = _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.7\n")
    if evicted == "note":
        (vault / "Sources" / f".{FRESH}.md.icloud").write_bytes(b"")
    else:
        (vault / "PDFs" / f".{FRESH}.pdf.icloud").write_bytes(b"")

    report = migrate(vault, apply=True)

    assert [r.status for r in report.rows] == ["conflict"]
    assert note.exists() and not (vault / "Sources" / f"{FRESH}.md").exists()
    assert (vault / "PDFs" / f"{STALE}.pdf").exists()


def test_an_evicted_note_is_reported_rather_than_scanned_past(vault: Path) -> None:
    # It matches no `*.md` glob, so without this a half-synced vault reports as
    # canonical — and that report is what an `--apply` is approved from.
    (vault / "Sources" / f".{STALE}.md.icloud").write_bytes(b"")

    report = migrate(vault, apply=False)

    assert [s.to_json() for s in report.skipped] == [
        {"path": f"Sources/.{STALE}.md.icloud", "reason": "icloud_placeholder"}
    ]


def test_a_taken_pdf_name_costs_the_whole_pair(vault: Path) -> None:
    # The note and its file move together or not at all: a note that moved alone
    # would point at a reading copy filed under a name nothing refers to.
    note = _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-mine\n")
    (vault / "PDFs" / f"{FRESH}.pdf").write_bytes(b"%PDF-someone else's\n")

    report = migrate(vault, apply=True)

    assert report.rows[0].status == "conflict"
    assert note.exists() and not (vault / "Sources" / f"{FRESH}.md").exists()
    assert (vault / "PDFs" / f"{STALE}.pdf").read_bytes() == b"%PDF-mine\n"
    assert (vault / "PDFs" / f"{FRESH}.pdf").read_bytes() == b"%PDF-someone else's\n"


def test_a_run_interrupted_after_the_file_moved_finishes_the_pair(vault: Path) -> None:
    # The PDF moves first so that a crash leaves the note where the scan looks
    # for it. Finding the target file already in place is that half-done state,
    # not a conflict.
    _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{FRESH}.pdf]]")
    (vault / "PDFs" / f"{FRESH}.pdf").write_bytes(b"%PDF-1.7\n")

    report = migrate(vault, apply=True)

    assert [r.status for r in report.rows] == ["renamed"]
    assert (vault / "Sources" / f"{FRESH}.md").exists()
    assert (vault / "PDFs" / f"{FRESH}.pdf").exists()


def test_a_web_page_reading_link_is_left_alone(vault: Path) -> None:
    # Only the embed of the note's own PDF is derived from the slug.
    _source(vault, STALE, STALE_URL, reading_link=STALE_URL)

    migrate(vault, apply=True)

    text = (vault / "Sources" / f"{FRESH}.md").read_text()
    assert f"reading_link: {STALE_URL}" in text


def test_a_barrier_that_cannot_be_cleared_stops_the_note_from_moving(vault: Path) -> None:
    # The flush between the two renames is a barrier, not a last step: shrugged
    # off, the note could land while its file did not — and the next scan, seeing
    # a canonical note, would never look for the file again.
    _source(vault, STALE, STALE_URL, source_type="pdf", reading_link=f"[[{STALE}.pdf]]")
    (vault / "PDFs" / f"{STALE}.pdf").write_bytes(b"%PDF-1.7\n")

    def refuse(directory: Path) -> None:
        raise OSError("this filesystem will not flush a directory")

    with mock.patch.object(migrate_mod, "fsync_dir", refuse):
        report = migrate(vault, apply=True)

    assert [r.status for r in report.rows] == ["failed"]
    assert (vault / "Sources" / f"{STALE}.md").exists(), "the note stays where the scan finds it"
    # The next pass takes the pair from the half-done state the failure left.
    assert [r.status for r in migrate(vault, apply=True).rows] == ["renamed"]
    assert (vault / "PDFs" / f"{FRESH}.pdf").exists()


def test_a_rename_that_cannot_land_costs_its_own_row(vault: Path) -> None:
    # A pass over a whole vault must report the note the filesystem refused and
    # go on, rather than ending on the first one — and the row that failed must
    # not be counted as renamed.
    _source(vault, STALE, STALE_URL)
    (vault / "Sources").chmod(0o500)  # readable, so the scan still sees the note
    try:
        report = migrate(vault, apply=True)
    finally:
        (vault / "Sources").chmod(0o700)

    assert [r.status for r in report.rows] == ["failed"]
    assert report.rows[0].detail
    assert report.unresolved == 1
    assert (vault / "Sources" / f"{STALE}.md").exists()


def test_a_missing_folder_is_not_an_error(vault: Path) -> None:
    # A vault without one of the note folders yields nothing for it rather than
    # raising; `kboat-doctor` is what insists the folders are there.
    for sub in ("Sources", "Repos", "Feeds"):
        (vault / sub).rmdir()

    assert migrate(vault, apply=False).rows == []
