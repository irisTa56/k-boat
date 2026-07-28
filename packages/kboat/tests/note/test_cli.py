"""End-to-end tests for the `kboat-note` CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kboat.lock import LOCK_NAME, vault_lock
from kboat.naming import note_slug, url_slug
from kboat.note.__main__ import main

# The writer verifies a record's slug against its `url`, so a record meant to
# reach the vault names its note the way a real writer does.
URL = "https://x"
SLUG = note_slug(URL)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for sub in ("Sources", "Kindles", "Repos"):
        (tmp_path / sub).mkdir()
    return tmp_path


def _run(argv: list[str], stdin: str, monkeypatch: pytest.MonkeyPatch) -> int:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return main(argv)


def test_write_source_creates(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = json.dumps(
        {
            "slug": SLUG,
            "fields": {
                "type": "source",
                "title": "T",
                "url": URL,
                "source_type": "web_page",
            },
        }
    )
    assert (
        _run(
            ["write", "--type", "source", "--vault", str(vault), "--today", "2026-06-13"],
            rec,
            monkeypatch,
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "created"
    assert (vault / "Sources" / f"{SLUG}.md").exists()


def test_collision_exits_nonzero(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 48-bit clash cannot be produced by hashing, so it is staged: the note for
    # one URL is moved to the slug the other names, which is the state a real
    # clash would leave.
    a = json.dumps(
        {
            "slug": note_slug("https://a"),
            "fields": {
                "type": "source",
                "url": "https://a",
                "source_type": "web_page",
                "title": "A",
            },
        }
    )
    _run(["write", "--type", "source", "--vault", str(vault)], a, monkeypatch)
    capsys.readouterr()
    clashing = note_slug("https://b")
    (vault / "Sources" / f"{note_slug('https://a')}.md").rename(
        vault / "Sources" / f"{clashing}.md"
    )
    b = json.dumps(
        {"slug": clashing, "fields": {"type": "source", "url": "https://b", "title": "B"}}
    )
    assert _run(["write", "--type", "source", "--vault", str(vault)], b, monkeypatch) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "collision"


def test_bad_input_and_usage(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The stderr line says which refusal it was, so the agent knows what to fix.
    assert _run(["write", "--type", "source", "--vault", str(vault)], "not json", monkeypatch) == 2
    assert "not valid JSON" in capsys.readouterr().err
    assert (
        _run(["write", "--type", "source", "--vault", str(vault)], '{"no":"slug"}', monkeypatch)
        == 2
    )
    assert "'slug' must be a non-empty string" in capsys.readouterr().err
    # A slug that is no filename is a refusal too, not a path the write follows.
    escaping = json.dumps({"slug": "../../evil", "fields": {"type": "source", "title": "T"}})
    assert _run(["write", "--type", "source", "--vault", str(vault)], escaping, monkeypatch) == 2
    assert "not a filename" in capsys.readouterr().err
    assert list(vault.parent.glob("evil.md")) == []
    # A content key the writer cannot read would otherwise land an empty note,
    # reported as written — and for a Kindle book the body is the write. The
    # same refusal `kboat-repos write` makes, since the two are one contract.
    bad_fields = json.dumps({"slug": "s1", "fields": "oops"})
    assert _run(["write", "--type", "source", "--vault", str(vault)], bad_fields, monkeypatch) == 2
    assert "'fields' must be a JSON object" in capsys.readouterr().err
    bad_body = json.dumps({"slug": "B1", "fields": {"type": "kindle"}, "body": ["a quote"]})
    assert _run(["write", "--type", "kindle", "--vault", str(vault)], bad_body, monkeypatch) == 2
    assert "'body' must be a string" in capsys.readouterr().err
    assert not (vault / "Sources" / "s1.md").exists()
    assert not (vault / "Kindles" / "B1.md").exists()
    assert main([]) == 0  # bare usage
    assert main(["bogus"]) == 2  # unknown subcommand


def test_refuses_a_locked_vault(
    vault: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    brief_lock_wait: None,
) -> None:
    rec = json.dumps(
        {
            "slug": SLUG,
            "fields": {
                "type": "source",
                "title": "T",
                "url": URL,
                "source_type": "web_page",
            },
        }
    )
    with vault_lock(vault):
        rc = _run(["write", "--type", "source", "--vault", str(vault)], rec, monkeypatch)
    assert rc == 1
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["status"] == "locked"
    assert out["holder"]["pid"] == os.getpid()
    assert "vault is locked" in captured.err
    assert not (vault / "Sources" / "s1.md").exists()


def test_an_unreadable_record_is_refused_before_the_lock_is_taken(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing about the vault would make this record writable, so it must not
    # cost the lock — and the exit code stays the record's own, not a refusal.
    bad = json.dumps({"slug": "s1", "fields": "oops"})
    assert _run(["write", "--type", "source", "--vault", str(vault)], bad, monkeypatch) == 2
    assert "'fields' must be a JSON object" in capsys.readouterr().err
    assert not (vault / LOCK_NAME).exists()


def test_slug_prints_the_oracle_for_one_url(capsys: pytest.CaptureFixture[str]) -> None:
    # No `--vault`: the oracle reads nothing and writes nothing, so a skill can
    # ask it before it knows whether the note exists.
    assert main(["slug", "HTTPS://Example.com/post/?utm_source=x#top"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "url": "HTTPS://Example.com/post/?utm_source=x#top",
        "canonical_url": "https://example.com/post",
        "slug": note_slug("https://example.com/post"),
    }


@pytest.mark.parametrize(
    "bad",
    ["https://example.com:99999/x", "http://[oops/a"],
    ids=["a port past 65535", "a malformed IPv6 literal"],
)
def test_a_url_no_parser_can_take_is_reported_not_raised(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    # Both commands take a URL the skills read off untrusted page text, so a
    # string with no canonical form at all is an input to report — exit 2, the
    # record-to-fix code — rather than a traceback with nothing on stdout.
    assert main(["slug", bad]) == 2
    assert "not a usable URL" in capsys.readouterr().err

    rec = json.dumps({"slug": "whatever", "fields": {"type": "source", "url": bad}})
    assert _run(["write", "--type", "source", "--vault", str(vault)], rec, monkeypatch) == 2
    assert "not a usable URL" in capsys.readouterr().err
    assert list((vault / "Sources").glob("*.md")) == []


def test_a_slug_that_is_not_the_one_the_url_names_is_refused(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The write contract's half of the identity guarantee: a note can only be
    # written under the name its own url gives it.
    rec = json.dumps(
        {
            "slug": "hand-picked",
            "fields": {"type": "source", "title": "T", "url": URL, "source_type": "web_page"},
        }
    )
    assert _run(["write", "--type", "source", "--vault", str(vault)], rec, monkeypatch) == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "status": "slug_mismatch",
        "identity": "url",
        "url": URL,
        "expected": SLUG,
        "got": "hand-picked",
    }
    assert list((vault / "Sources").glob("*.md")) == []


def test_migrate_slugs_reports_then_renames(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = url_slug(URL)  # the pre-canonical recipe: the stored url hashed as-is
    (vault / "Sources" / f"{stale}.md").write_text(
        f"---\ntype: source\ntitle: T\nurl: {URL}\n---\n", encoding="utf-8"
    )

    assert main(["migrate-slugs", "--vault", str(vault), "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["apply"] is False
    assert [(r["current"], r["expected"]) for r in report["rows"]] == [(stale, SLUG)]
    assert (vault / "Sources" / f"{stale}.md").exists(), "a dry run moves nothing"

    assert main(["migrate-slugs", "--vault", str(vault), "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["renamed"] == 1
    assert (vault / "Sources" / f"{SLUG}.md").exists()


def test_migrate_slugs_says_on_stderr_that_it_skipped_notes(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A dry run is read for its exit code before its JSON, and a skipped note is
    # one the oracle could not answer for — silence would pass the vault off as
    # canonical when part of it was never examined. Not an exit code: nothing
    # about a skipped note stops the rest of the pass.
    (vault / "Sources" / "broken.md").write_text("no frontmatter here\n", encoding="utf-8")

    assert main(["migrate-slugs", "--vault", str(vault), "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "1 note(s) skipped" in captured.err
    assert json.loads(captured.out)["skipped"][0]["path"] == "Sources/broken.md"


def test_migrate_slugs_exits_nonzero_on_a_conflict(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = url_slug(URL)
    for slug in (stale, SLUG):
        (vault / "Sources" / f"{slug}.md").write_text(
            f"---\ntype: source\ntitle: T\nurl: {URL}\n---\n", encoding="utf-8"
        )

    assert main(["migrate-slugs", "--vault", str(vault), "--apply"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["counts"]["conflicts"] == 1
    assert "not renamed" in captured.err


def test_a_conflict_on_a_note_this_pass_renamed_clears_on_the_next(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The chain the stderr advice and the spec both promise: the plan is computed
    # before anything moves, so a note whose target is occupied by another
    # *pending* note conflicts once and renames on the pass after.
    other = "https://elsewhere.test/a/"
    (vault / "Sources" / "stale-name.md").write_text(
        f"---\ntype: source\ntitle: A\nurl: {URL}\n---\n", encoding="utf-8"
    )
    (vault / "Sources" / f"{SLUG}.md").write_text(
        f"---\ntype: source\ntitle: B\nurl: {other}\n---\n", encoding="utf-8"
    )

    assert main(["migrate-slugs", "--vault", str(vault), "--apply"]) == 1
    captured = capsys.readouterr()
    counts = json.loads(captured.out)["counts"]
    assert (counts["renamed"], counts["conflicts"]) == (1, 1)
    assert "so re-run" in captured.err

    assert main(["migrate-slugs", "--vault", str(vault), "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["renamed"] == 1
    assert (vault / "Sources" / f"{SLUG}.md").read_text().count("title: A") == 1
    assert (vault / "Sources" / f"{note_slug(other)}.md").exists()


def test_migrate_slugs_requires_a_mode(vault: Path) -> None:
    # Neither reporting nor renaming is a safe default, so a bare invocation is a
    # usage error rather than the one that moves files.
    with pytest.raises(SystemExit) as exit_info:
        main(["migrate-slugs", "--vault", str(vault)])
    assert exit_info.value.code == 2


def test_migrate_slugs_refuses_a_locked_vault_but_reads_one_freely(
    vault: Path, capsys: pytest.CaptureFixture[str], brief_lock_wait: None
) -> None:
    with vault_lock(vault):
        assert main(["migrate-slugs", "--vault", str(vault), "--apply"]) == 1
        captured = capsys.readouterr()
        assert json.loads(captured.out)["status"] == "locked"
        assert "vault is locked" in captured.err
        # The dry run is read-only, so it neither waits nor is refused.
        assert main(["migrate-slugs", "--vault", str(vault), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == []


def test_migrate_slugs_refuses_a_vault_that_is_not_there_in_either_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A mis-typed `--vault` scans no folders, so a dry run over it would report a
    # vault with nothing to migrate — and that report is what an `--apply` gets
    # approved from.
    missing = tmp_path / "typo-vault"

    for mode in ("--dry-run", "--apply"):
        assert main(["migrate-slugs", "--vault", str(missing), mode]) == 1
        captured = capsys.readouterr()
        assert captured.out == "", mode
        assert "no vault at" in captured.err, mode


def test_migrate_slugs_reports_a_lock_it_cannot_operate(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A lock that cannot be taken at all has no holder to come back for, so it
    # gets the report-shaped CLIs' `vault lock unavailable:` line and an empty
    # stdout — not a `locked` record a caller would read as worth retrying.
    (vault / LOCK_NAME).mkdir()

    assert main(["migrate-slugs", "--vault", str(vault), "--apply"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "vault lock unavailable:" in captured.err


def test_a_vault_root_that_does_not_exist_is_reported_not_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A mis-typed `--vault` used to grow an empty vault beside the real one. The
    # lock is taken before anything is written, so it fails there instead.
    missing = tmp_path / "typo-vault"
    rec = json.dumps(
        {
            "slug": SLUG,
            "fields": {
                "type": "source",
                "title": "T",
                "url": URL,
                "source_type": "web_page",
            },
        }
    )
    assert _run(["write", "--type", "source", "--vault", str(missing)], rec, monkeypatch) == 1
    assert "write failed:" in capsys.readouterr().err
    assert not missing.exists()
