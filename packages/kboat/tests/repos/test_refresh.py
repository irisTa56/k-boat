"""Tests for `refresh` — its in-place field rewrite, the metadata update,
judgement/body preservation, and the adopt-rename of renamed/transferred/
case-changed repos. `gh` is monkeypatched."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

import kboat.repos.refresh as refresh_mod
from kboat.frontmatter import FrontmatterError, body_after_frontmatter, parse_frontmatter
from kboat.lock import vault_lock
from kboat.repos.identity import canonical_slug
from kboat.repos.refresh import main as refresh_main
from kboat.repos.refresh import refresh, set_fields
from kboat.schema import REPO
from kboat.write import upsert

TODAY = date(2026, 6, 6)


def _note_fields(url: str, title: str) -> dict[str, object]:
    return {
        "type": "repo",
        "title": title,
        "url": url,
        "homepage": "",
        "reading": True,
        "description": "old desc",
        "language": ["Go"],
        "topics": ["x"],
        "stars": 1,
        "archived": False,
        "created_at": "2024-01-01",
        "last_commit": "2024-01-01",
        "license": "mit",
        "role": "library",
        "domain": ["devtools"],
        "summary": "要約",
        "status": "dormant",
    }


def _write_note(vault: Path, url: str, title: str, body: str = "keep me") -> Path:
    """A pre-existing repo note, laid down by the writer refresh reads back."""
    slug = canonical_slug(url)
    assert slug
    upsert(
        REPO,
        vault,
        {"slug": slug, "fields": _note_fields(url, title), "body": body},
        today="2024-01-01",
    )
    return vault / "Repos" / f"{slug}.md"


def _meta(owner: str, name: str) -> dict:
    return {
        "owner": {"login": owner},
        "name": name,
        "description": "fresh desc",
        "primaryLanguage": {"name": "Go"},
        "languages": [{"node": {"name": "Go"}, "size": 100}],
        "repositoryTopics": [{"name": "y"}],
        "licenseInfo": {"key": "mit"},
        "isArchived": False,
        "pushedAt": "2026-06-01T00:00:00Z",
        "homepageUrl": "",
        "createdAt": "2024-01-01T00:00:00Z",
        "stargazerCount": 42,
    }


def test_set_fields_preserves_other_fields_and_body(tmp_path: Path) -> None:
    note = _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool").read_text()

    updated = set_fields(note, {"stars": 99999, "status": "dormant", "topics": ["x", "y"]})

    fm = parse_frontmatter(updated)
    assert fm["stars"] == "99999"
    assert fm["status"] == "dormant"
    assert "topics: [x, y]" in updated
    # Untouched judgement + body survive.
    assert fm["role"] == "library"
    assert fm["summary"] == "要約"
    assert body_after_frontmatter(updated).strip().endswith("keep me")


def test_set_fields_missing_key_raises(tmp_path: Path) -> None:
    # A key that is not already a top-level line is a note to look at, not a
    # field to insert — refresh only ever rewrites fields every note carries.
    note = _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool").read_text()
    with pytest.raises(FrontmatterError):
        set_fields(note, {"nonexistent_field": "x"})


def test_refresh_updates_metadata_preserves_judgement_and_body(tmp_path: Path, monkeypatch) -> None:
    _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (_meta(o, r), None))

    report = refresh(tmp_path, today=TODAY)
    assert report["counts"] == {
        "total": 1,
        "updated": 1,
        "adopted": 0,
        "rename_collisions": 0,
        "failed": 0,
        "anomalies": 0,
    }
    note = (tmp_path / "Repos" / f"{canonical_slug('https://github.com/acme/tool')}.md").read_text()
    fm = parse_frontmatter(note)
    assert fm["stars"] == "42"  # refreshed
    assert fm["status"] == "recent"  # recomputed from pushedAt
    assert fm["role"] == "library" and fm["summary"] == "要約"  # judgement preserved
    assert fm["reading"] is True  # preserved
    assert "keep me" in note  # body preserved


def test_refresh_adopts_rename_and_moves_file(tmp_path: Path, monkeypatch) -> None:
    old = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")
    # gh resolves to the new owner (transfer) regardless of the queried owner.
    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )

    report = refresh(tmp_path, today=TODAY)
    assert report["counts"]["adopted"] == 1
    assert not old.exists()  # old slug file removed
    new_path = tmp_path / "Repos" / f"{canonical_slug('https://github.com/a2aproject/A2A')}.md"
    # The reported paths are the contract the skill relays to the human, so they
    # are pinned alongside the on-disk move rather than left to inspection.
    rel_new = new_path.relative_to(tmp_path).as_posix()
    assert report["adopted"][0]["to"] == rel_new
    assert report["adopted"][0]["from"] == old.relative_to(tmp_path).as_posix()
    assert report["updated"] == [rel_new]
    fm = parse_frontmatter(new_path.read_text())
    assert fm["url"] == "https://github.com/a2aproject/A2A"
    assert fm["title"] == "a2aproject/A2A"
    assert fm["role"] == "library"  # judgement carried over to the renamed note


def test_refresh_adopts_case_only_rename(tmp_path: Path, monkeypatch) -> None:
    old = _write_note(
        tmp_path, "https://github.com/dylanblakemore/depscheck", "dylanblakemore/depscheck"
    )
    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("DylanBlakemore", "depscheck"), None)
    )
    report = refresh(tmp_path, today=TODAY)
    assert report["counts"]["adopted"] == 1
    assert not old.exists()
    new_path = (
        tmp_path / "Repos" / f"{canonical_slug('https://github.com/DylanBlakemore/depscheck')}.md"
    )
    assert parse_frontmatter(new_path.read_text())["title"] == "DylanBlakemore/depscheck"


def test_refresh_rename_collision_keeps_both(tmp_path: Path, monkeypatch) -> None:
    # Two notes; one would rename onto the other's slug.
    _write_note(tmp_path, "https://github.com/a2aproject/A2A", "a2aproject/A2A")  # canonical target
    old = _write_note(
        tmp_path, "https://github.com/google/A2A", "google/A2A"
    )  # will try to adopt a2aproject/A2A
    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )

    report = refresh(tmp_path, today=TODAY)
    assert report["counts"]["rename_collisions"] == 1
    assert report["counts"]["adopted"] == 0
    assert old.exists()  # not moved; both notes remain for a human to merge
    # The conflict names the slug that was already taken, and the refreshed note
    # is reported under the identity it kept.
    taken = f"Repos/{canonical_slug('https://github.com/a2aproject/A2A')}.md"
    collision = report["rename_collisions"][0]
    assert collision["conflict"] == taken
    assert collision["path"] == old.relative_to(tmp_path).as_posix()
    assert collision["path"] in report["updated"]
    # The collided note still got its metadata refreshed, but kept its old identity.
    assert parse_frontmatter(old.read_text())["url"] == "https://github.com/google/A2A"


def test_refresh_dryrun_reports_same_target_collapse_consistently(
    tmp_path: Path, monkeypatch
) -> None:
    # Two distinct notes that both resolve (via gh) to one canonical repo: even in
    # dry-run, exactly one adopts and the other is a rename collision — not two adopts.
    _write_note(tmp_path, "https://github.com/acme/old-a", "acme/old-a")
    _write_note(tmp_path, "https://github.com/acme/old-b", "acme/old-b")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (_meta("acme", "merged"), None))

    report = refresh(tmp_path, today=TODAY, dry_run=True)
    assert report["counts"]["adopted"] == 1
    assert report["counts"]["rename_collisions"] == 1
    # Both notes still on disk (dry-run wrote nothing).
    assert (tmp_path / "Repos" / f"{canonical_slug('https://github.com/acme/old-a')}.md").exists()
    assert (tmp_path / "Repos" / f"{canonical_slug('https://github.com/acme/old-b')}.md").exists()


def test_refresh_reports_failed_repo(tmp_path: Path, monkeypatch) -> None:
    _write_note(tmp_path, "https://github.com/acme/gone", "acme/gone")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (None, "not found"))
    report = refresh(tmp_path, today=TODAY)
    assert report["counts"]["failed"] == 1
    assert report["failed"][0]["owner_repo"] == "acme/gone"


def _counts_match_the_lists(report: dict) -> bool:
    """Every count is the length of the list it summarises (`total` aside)."""
    return all(
        report["counts"][key] == len(report[key]) for key in report["counts"] if key != "total"
    )


def test_refresh_isolates_an_unreadable_payload_to_the_one_note(
    tmp_path: Path, monkeypatch
) -> None:
    # A payload the mapping cannot read fails that note alone: the rest of the catalogue
    # is still refreshed, and the run reports exactly that one failure. The failing note
    # would also have adopted a rename, so this pins the accounting too — it must not
    # appear in `updated` or `adopted` on the strength of work that never completed.
    # The mapping is stubbed rather than fed a known-bad shape, for the reason
    # `test_gather_reports_an_unmappable_gh_payload_as_a_non_retryable_defect` gives.
    good = _write_note(tmp_path, "https://github.com/acme/good", "acme/good")
    bad = _write_note(tmp_path, "https://github.com/acme/old", "acme/old")
    before = bad.read_text(encoding="utf-8")
    real_github_fields = refresh_mod.github_fields

    def flaky(meta: dict, *, today: date) -> dict[str, object]:
        if meta["name"] == "renamed":
            raise KeyError("licenseInfo")
        return real_github_fields(meta, today=today)

    # `acme/old` resolves to a new name (a rename to adopt); everything else is itself.
    monkeypatch.setattr(
        refresh_mod,
        "gh_repo_view",
        lambda o, r: (_meta(o, "renamed" if r == "old" else r), None),
    )
    monkeypatch.setattr(refresh_mod, "github_fields", flaky)

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"] == {
        "total": 2,
        "updated": 1,
        "adopted": 0,
        "rename_collisions": 0,
        "failed": 1,
        "anomalies": 0,
    }
    assert _counts_match_the_lists(report)
    failure = report["failed"][0]
    assert failure["owner_repo"] == "acme/old"
    assert failure["path"] == bad.relative_to(tmp_path).as_posix()
    assert "unreadable gh payload" in failure["error"]
    # The other note went through, and the failing one is in no other list.
    assert report["updated"] == [good.relative_to(tmp_path).as_posix()]
    assert report["adopted"] == []
    assert parse_frontmatter(good.read_text())["stars"] == "42"
    # Untouched and still at its old slug, so the next run retries it as it stands.
    assert bad.read_text(encoding="utf-8") == before
    assert not (
        tmp_path / "Repos" / f"{canonical_slug('https://github.com/acme/renamed')}.md"
    ).exists()


def test_refresh_does_not_report_a_rename_it_failed_to_write(tmp_path: Path, monkeypatch) -> None:
    # `adopted` is the set of renames healed, so a note whose rewrite failed belongs in
    # `failed` and nowhere else — otherwise the report claims a move that never happened.
    old = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")
    before = old.read_text(encoding="utf-8")

    def unwritable(path: Path, content: str) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )
    monkeypatch.setattr(refresh_mod, "atomic_write_text", unwritable)

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["failed"] == 1
    assert report["counts"]["adopted"] == 0
    assert report["counts"]["updated"] == 0
    assert _counts_match_the_lists(report)
    assert "write failed:" in report["failed"][0]["error"]
    assert old.read_text(encoding="utf-8") == before  # still there, still itself


def test_an_applying_run_holds_the_lock_over_its_whole_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_is_held: Callable[[Path], bool]
) -> None:
    # The hold spans the pass, fetch included: the simple placement, and what makes
    # the read-modify-write of every note serialized without reshaping `refresh`.
    held: dict[str, bool] = {}
    reads: list[bool] = []

    def fake_gh(owner: str, name: str) -> tuple[dict, str | None]:
        held["during_fetch"] = lock_is_held(tmp_path)
        return _meta(owner, name), None

    real_read_text = Path.read_text

    def spy_read_text(self: Path, *args: object, **kwargs: object) -> str:
        # The identity each note is fetched under is read here, before the fetch. It is
        # what makes the pass one read-modify-write, so it has to be inside the hold
        # too — and it is the read a narrower placement would have left outside.
        if self.parent == tmp_path / "Repos":
            reads.append(lock_is_held(tmp_path))
        return real_read_text(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    real_set_fields = refresh_mod.set_fields

    def spy_set_fields(text: str, updates: object) -> str:
        # `set_fields` is handed the note's freshly-read text, so this is the read and
        # the write it feeds, both inside the hold.
        held["during_rewrite"] = lock_is_held(tmp_path)
        return real_set_fields(text, updates)  # ty: ignore[invalid-argument-type]

    _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", fake_gh)
    monkeypatch.setattr(refresh_mod, "set_fields", spy_set_fields)
    monkeypatch.setattr(Path, "read_text", spy_read_text)

    assert refresh_main(["--vault", str(tmp_path), "--today", "2026-06-06"]) == 0

    assert held == {"during_fetch": True, "during_rewrite": True}
    assert reads and all(reads), "every Repos/ read must happen while the lock is held"
    assert not lock_is_held(tmp_path), "the hold ends with the run"


def test_a_dry_run_reads_a_held_vault_and_rewrites_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The dry-run guard is in `main`, not in `refresh`, so this has to go through the
    # CLI: calling `refresh(dry_run=True)` directly would assert only that the library
    # function is lock-free, which it is in every branch. Held throughout, so dropping
    # the guard turns this into a refusal.
    fetched: list[str] = []

    def fake_gh(owner: str, name: str) -> tuple[dict, str | None]:
        fetched.append(f"{owner}/{name}")
        return _meta(owner, name), None

    note = _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    before = note.read_text(encoding="utf-8")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", fake_gh)

    with vault_lock(tmp_path):
        assert refresh_main(["--vault", str(tmp_path), "--dry-run", "--today", "2026-06-06"]) == 0

    out = json.loads(capsys.readouterr().out)
    # A dry run still fetches and still reports what it would change — otherwise this
    # would pass for a run that did nothing at all, which is not the same thing.
    assert fetched == ["acme/tool"]
    assert out["counts"]["updated"] == 1
    assert note.read_text(encoding="utf-8") == before


def test_a_held_vault_is_refused_before_anything_is_fetched_or_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, brief_lock_wait: None
) -> None:
    # The lock is taken first, so a held vault costs no `gh` call and leaves the note
    # exactly as it was.
    fetched: list[str] = []

    def fake_gh(owner: str, name: str) -> tuple[dict, str | None]:
        fetched.append(f"{owner}/{name}")
        return _meta(owner, name), None

    note = _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    before = note.read_text(encoding="utf-8")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", fake_gh)

    with vault_lock(tmp_path):
        assert refresh_main(["--vault", str(tmp_path), "--today", "2026-06-06"]) == 1
    assert fetched == []
    assert note.read_text(encoding="utf-8") == before


def test_the_cli_reports_a_locked_vault_in_place_of_its_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    brief_lock_wait: None,
) -> None:
    # With a note to rewrite, the hold is taken and the refusal is what stdout
    # carries — the report the skill would have parsed is not there.
    _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, n: (_meta(o, n), None))
    with vault_lock(tmp_path):
        rc = refresh_main(["--vault", str(tmp_path), "--today", "2026-06-06"])
    assert rc == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "locked"
    assert "vault is locked" in captured.err


def test_the_cli_reports_a_vault_whose_lock_cannot_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `refresh` also shells out to `gh`, whose own failures are `OSError` too — which
    # is why an unusable lock carries its own type rather than being caught as one.
    # Reported on stderr with an empty stdout, and no `locked` record to retry on.
    _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, n: (_meta(o, n), None))
    tmp_path.chmod(0o555)
    try:
        rc = refresh_main(["--vault", str(tmp_path), "--today", "2026-06-06"])
    finally:
        tmp_path.chmod(0o755)
    assert rc == 1
    captured = capsys.readouterr()
    assert "vault lock unavailable" in captured.err
    assert captured.out == ""
