"""Tests for `refresh` — its in-place field rewrite, the metadata update,
judgement/body preservation, and the adopt-rename of renamed/transferred/
case-changed repos. `gh` is monkeypatched."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

import kboat.repos.gather as gather_mod
import kboat.repos.refresh as refresh_mod
from kboat.frontmatter import FrontmatterError, body_after_frontmatter, parse_frontmatter
from kboat.lock import vault_lock
from kboat.repos.gather import PayloadError
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


def test_an_adopted_rename_names_the_stub_it_strands(tmp_path: Path, monkeypatch) -> None:
    # This pass runs unattended in the daily routine, so nobody approves the move.
    # The stub it vacates becomes a lone placeholder, which fails `icloud_notes`
    # and stops every later run — the least this owes is to name what it left.
    old = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")
    (tmp_path / "Repos" / f".{old.stem}.md.icloud").write_bytes(b"")
    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["adopted"] == 1
    assert report["adopted"][0]["stranded"] == f"Repos/.{old.stem}.md.icloud"


def test_an_adopt_that_vacates_nothing_reports_no_stranded_stub(
    tmp_path: Path, monkeypatch
) -> None:
    # A reserved owner gives no canonical slug, so the note adopts the new identity
    # under the name it already has. Nothing is vacated, the stub is not lone, and
    # the skill routes `stranded` to "the next doctor stops the routine" — an alarm
    # about a stoppage that cannot happen.
    old = _write_note(tmp_path, "https://github.com/oldowner/thing", "oldowner/thing")
    (tmp_path / "Repos" / f".{old.stem}.md.icloud").write_bytes(b"")
    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("security", "thing"), None)
    )

    report = refresh(tmp_path, today=TODAY)

    entry = report["adopted"][0]
    assert entry["from"] == entry["to"], "nothing moved"
    assert "stranded" not in entry


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
    assert collision["reason"] == "taken"  # a note on disk; a human merges the two
    assert collision["path"] == old.relative_to(tmp_path).as_posix()
    assert collision["path"] in report["updated"]
    # The collided note still got its metadata refreshed, but kept its old identity.
    assert parse_frontmatter(old.read_text())["url"] == "https://github.com/google/A2A"


def test_refresh_rename_collision_when_the_target_is_an_icloud_placeholder(
    tmp_path: Path, monkeypatch
) -> None:
    # The vault is iCloud-synced, so a note evicted from the target name is not
    # gone: `exists()` says `False` for it exactly as it would for a free name.
    # Renaming onto it would claim an identity another note still holds, and
    # iCloud would settle the two later by suffixing or dropping one.
    old = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")
    taken_slug = canonical_slug("https://github.com/a2aproject/A2A")
    assert taken_slug
    (tmp_path / "Repos" / f".{taken_slug}.md.icloud").write_bytes(b"")
    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["rename_collisions"] == 1
    assert report["counts"]["adopted"] == 0
    assert report["rename_collisions"][0]["conflict"] == f"Repos/{taken_slug}.md"
    # Named, not inferred: the remedy is the download, not a merge.
    assert report["rename_collisions"][0]["reason"] == "evicted"
    assert not (tmp_path / "Repos" / f"{taken_slug}.md").exists()
    # Refreshed in place under the identity it kept, like any other collision.
    assert old.exists()
    assert parse_frontmatter(old.read_text())["url"] == "https://github.com/google/A2A"


def test_refresh_reports_an_evicted_note_as_an_anomaly(tmp_path: Path, monkeypatch) -> None:
    # An evicted note matches no `*.md` glob, so without this the pass would
    # report a full refresh of the half of the catalogue that happened to be local.
    good = _write_note(tmp_path, "https://github.com/acme/good", "acme/good")
    (tmp_path / "Repos" / ".0123456789ab.md.icloud").write_bytes(b"")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (_meta(o, r), None))

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["anomalies"] == 1
    assert report["anomalies"][0]["path"] == "Repos/.0123456789ab.md.icloud"
    assert report["counts"]["total"] == 1  # the evicted note never became one
    assert report["updated"] == [good.relative_to(tmp_path).as_posix()]


def test_a_broken_symlink_at_the_target_collides_and_files_an_anomaly(
    tmp_path: Path, monkeypatch
) -> None:
    # The pair the skill's triage routes on: a `conflict` path with no file and no
    # placeholder is a name held by something that is not a note, and the anomaly
    # is what separates it from the two self-clearing readings. Both halves are
    # asserted here, because the skill tells the agent to look for the second.
    old = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")
    taken_slug = canonical_slug("https://github.com/a2aproject/A2A")
    assert taken_slug
    (tmp_path / "Repos" / f"{taken_slug}.md").symlink_to(tmp_path / "Repos" / "nowhere.md")
    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )

    report = refresh(tmp_path, today=TODAY)

    conflict = report["rename_collisions"][0]["conflict"]
    assert conflict == f"Repos/{taken_slug}.md"
    assert not (tmp_path / conflict).exists()
    assert not (tmp_path / "Repos" / f".{taken_slug}.md.icloud").exists()
    assert report["rename_collisions"][0]["reason"] == "held_by_non_note"
    assert [a["path"] for a in report["anomalies"]] == [conflict]
    assert report["adopted"] == []  # nothing this run vacates it later
    assert old.exists()


def test_a_broken_symlink_with_a_stale_stub_beside_it_is_not_reported_as_evicted(
    tmp_path: Path, monkeypatch
) -> None:
    # The name itself is asked before the placeholder beside it. Answering
    # `evicted` here would say the merge waits on a download, and no download
    # frees a symlink — `evicted` is the one reason the skill routes to nobody,
    # so the collision would be re-reported every run with no one ever asked.
    old = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")
    taken_slug = canonical_slug("https://github.com/a2aproject/A2A")
    assert taken_slug
    target = tmp_path / "Repos" / f"{taken_slug}.md"
    target.symlink_to(tmp_path / "Repos" / "nowhere.md")
    (tmp_path / "Repos" / f".{taken_slug}.md.icloud").write_text("")
    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )

    report = refresh(tmp_path, today=TODAY)

    assert report["rename_collisions"][0]["reason"] == "held_by_non_note"
    assert old.exists()


def test_a_file_beside_its_own_placeholder_reports_taken_not_evicted(
    tmp_path: Path, monkeypatch
) -> None:
    # The two causes are not exclusive, so the order is a precedence: `evicted`
    # would say the merge waits on a download that already happened, and the
    # duplicate would sit there being re-reported that way every run.
    _write_note(tmp_path, "https://github.com/a2aproject/A2A", "a2aproject/A2A")
    _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")
    taken_slug = canonical_slug("https://github.com/a2aproject/A2A")
    assert taken_slug
    (tmp_path / "Repos" / f".{taken_slug}.md.icloud").write_bytes(b"")
    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )

    report = refresh(tmp_path, today=TODAY)

    collision = next(
        c for c in report["rename_collisions"] if c["conflict"] == f"Repos/{taken_slug}.md"
    )
    assert collision["reason"] == "taken"


def test_a_repos_directory_whose_own_stat_is_refused_is_an_anomaly_not_an_absence(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # The gate was `is_dir()`, which swallows the refusal on 3.14 and raises on
    # 3.13 — so this reported "no Repos/ directory under vault" for one that is
    # there, in a report with no counts and no anomalies for either whole-report
    # escalation rule to fire on.
    walled = tmp_path_factory.mktemp("walled")
    (walled / "Repos").mkdir()
    (tmp_path / "Repos").symlink_to(walled / "Repos")
    walled.chmod(0o000)
    try:
        report = refresh(tmp_path, today=TODAY)
    finally:
        walled.chmod(0o755)

    assert "error" not in report
    assert report["counts"]["anomalies"] == 1
    assert report["anomalies"][0]["path"] == "Repos"


def test_a_repos_name_taken_by_a_file_is_an_error_not_an_empty_catalogue(
    tmp_path: Path,
) -> None:
    # `list_note_dir` reads a non-directory as empty, so dropping the gate outright
    # would turn this into a green report over a catalogue that cannot exist.
    (tmp_path / "Repos").write_text("not a directory\n", encoding="utf-8")

    report = refresh(tmp_path, today=TODAY)

    assert "is not a directory" in report["error"]


def test_refresh_says_so_when_the_catalogue_cannot_be_listed(tmp_path: Path) -> None:
    # The wrong answer is a green empty report: neither whole-report escalation
    # rule fires on one, since both need failures or anomalies to fire on.
    _write_note(tmp_path, "https://github.com/acme/good", "acme/good")
    (tmp_path / "Repos").chmod(0o111)
    try:
        report = refresh(tmp_path, today=TODAY)
    finally:
        (tmp_path / "Repos").chmod(0o755)

    assert report["counts"] == {
        "total": 0,
        "updated": 0,
        "adopted": 0,
        "rename_collisions": 0,
        "failed": 0,
        "anomalies": 1,
    }
    assert report["anomalies"][0]["path"] == "Repos"
    assert "could not be listed" in report["anomalies"][0]["error"]


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
    # This run claimed the slug, so no file is at the conflict path in a dry run —
    # the reason says so rather than leaving the reader to find nothing there.
    assert report["rename_collisions"][0]["reason"] == "claimed_this_run"
    # Both notes still on disk (dry-run wrote nothing).
    assert (tmp_path / "Repos" / f"{canonical_slug('https://github.com/acme/old-a')}.md").exists()
    assert (tmp_path / "Repos" / f"{canonical_slug('https://github.com/acme/old-b')}.md").exists()


def test_refresh_reports_failed_repo(tmp_path: Path, monkeypatch) -> None:
    _write_note(tmp_path, "https://github.com/acme/gone", "acme/gone")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (None, "not found"))
    report = refresh(tmp_path, today=TODAY)
    assert report["counts"]["failed"] == 1
    assert report["failed"][0]["owner_repo"] == "acme/gone"
    # `fetch` is the escalation switch's off position, and this is its commonest
    # member: a repo that is gone answers non-zero every day, and a run that read
    # it as the permanent class would notify about it every day.
    assert report["failed"][0]["reason"] == "fetch"


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
    # `reason` is what the run branches on to escalate; `error` only carries detail.
    assert failure["reason"] == "payload"
    assert "licenseInfo" in failure["error"]
    # The other note went through, and the failing one is in no other list.
    assert report["updated"] == [good.relative_to(tmp_path).as_posix()]
    assert report["adopted"] == []
    assert parse_frontmatter(good.read_text())["stars"] == "42"
    # Untouched and still at its old slug, so the next run retries it as it stands.
    assert bad.read_text(encoding="utf-8") == before
    assert not (
        tmp_path / "Repos" / f"{canonical_slug('https://github.com/acme/renamed')}.md"
    ).exists()


def test_refresh_isolates_a_note_that_is_not_utf8(tmp_path: Path, monkeypatch) -> None:
    # The load reads every note before the pass begins, so a note that is not UTF-8
    # would end the run there — ahead of every per-note boundary, and with no report.
    # It is an anomaly for a human to repair, and the rest of the catalogue refreshes.
    good = _write_note(tmp_path, "https://github.com/acme/good", "acme/good")
    broken = tmp_path / "Repos" / "0123456789ab.md"
    broken.write_bytes(b"---\ntype: repo\nurl: https://github.com/a/b\n---\n\n\xff\xfe\n")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (_meta(o, r), None))

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["anomalies"] == 1
    assert report["anomalies"][0]["path"] == broken.relative_to(tmp_path).as_posix()
    assert report["counts"]["total"] == 1  # the unreadable note never became one
    assert report["updated"] == [good.relative_to(tmp_path).as_posix()]
    assert parse_frontmatter(good.read_text())["stars"] == "42"


def test_refresh_never_writes_an_unrecognised_payload_over_a_good_note(
    tmp_path: Path, monkeypatch
) -> None:
    # The realizable form: `gh` answers, with content, in a shape the mapping does not
    # know. Every field reads through a default, so letting it past wipes description,
    # stars, topics and license off every note in the catalogue and reports them all
    # refreshed. This one goes through the real `gh_repo_view`, since that is where the
    # shape is refused — a stub would prove nothing about the path a run takes.
    note = _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    before = note.read_text(encoding="utf-8")

    class _Completed:
        stdout = '{"data": {"name": "tool", "stargazerCount": 42}}'
        returncode = 0
        stderr = ""

    monkeypatch.setattr(gather_mod.subprocess, "run", lambda *a, **kw: _Completed())

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["updated"] == 0
    assert report["failed"][0]["reason"] == "payload"
    assert note.read_text(encoding="utf-8") == before


def test_refresh_does_not_escalate_a_gh_that_failed_without_a_message(
    tmp_path: Path, monkeypatch
) -> None:
    # A `gh` the OOM killer took, or one that wrote its diagnostic to stdout, exits
    # non-zero with an empty stderr. It is still a fetch that failed — reading the
    # absence of text as "answered unusably" would escalate a transient failure, and
    # go on escalating it every day the condition lasts.
    _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")

    class _Completed:
        stdout = ""
        returncode = 1
        stderr = "  \n"

    monkeypatch.setattr(gather_mod.subprocess, "run", lambda *a, **kw: _Completed())

    report = refresh(tmp_path, today=TODAY)

    assert report["failed"][0]["reason"] == "fetch"
    assert report["failed"][0]["error"]  # never an empty string for a human to read


def test_refresh_treats_an_unusable_gh_answer_as_the_payload_class(
    tmp_path: Path, monkeypatch
) -> None:
    # `gh` exiting zero with something unusable is settled where it is detected, and it
    # reaches the report as the class no later run clears — not as a fetch that failed.
    _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")

    def unusable(owner: str, name: str) -> tuple[dict, str | None]:
        raise PayloadError("gh returned unparseable JSON: line 1")

    monkeypatch.setattr(refresh_mod, "gh_repo_view", unusable)

    report = refresh(tmp_path, today=TODAY)

    assert report["failed"][0]["reason"] == "payload"
    assert "unparseable JSON" in report["failed"][0]["error"]


def test_refresh_leaves_a_slug_free_when_the_rename_that_wanted_it_failed(
    tmp_path: Path, monkeypatch
) -> None:
    # Two notes resolving to one canonical repo, the first of which cannot be written.
    # Its slug was never taken, so the second adopts it — rather than being reported as
    # colliding with a file that does not exist, which would send a human to merge one
    # note with nothing.
    _write_note(tmp_path, "https://github.com/acme/old-a", "acme/old-a")
    _write_note(tmp_path, "https://github.com/acme/old-b", "acme/old-b")
    real_write = refresh_mod.atomic_write_text
    writes: list[Path] = []

    def failing_first_write(path: Path, content: str) -> None:
        writes.append(path)
        if len(writes) == 1:
            raise OSError("read-only file system")
        real_write(path, content)

    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (_meta("acme", "merged"), None))
    monkeypatch.setattr(refresh_mod, "atomic_write_text", failing_first_write)

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["failed"] == 1
    assert report["counts"]["adopted"] == 1
    assert report["counts"]["rename_collisions"] == 0
    merged = tmp_path / "Repos" / f"{canonical_slug('https://github.com/acme/merged')}.md"
    assert merged.exists()


def test_refresh_isolates_a_gh_call_that_raises(tmp_path: Path, monkeypatch) -> None:
    # The fetch runs in a worker thread, and a raise there would surface in the parent
    # as the pass ending — no report at all, the healthy notes unprocessed. A `gh` that
    # stalls past its timeout, or is missing from PATH, is one note's failure.
    good = _write_note(tmp_path, "https://github.com/acme/good", "acme/good")
    stalled = _write_note(tmp_path, "https://github.com/acme/stalls", "acme/stalls")

    def fake_gh(owner: str, name: str) -> tuple[dict, str | None]:
        if name == "stalls":
            raise TimeoutError("gh timed out")
        return _meta(owner, name), None

    monkeypatch.setattr(refresh_mod, "gh_repo_view", fake_gh)

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["total"] == 2
    assert report["counts"]["failed"] == 1
    assert _counts_match_the_lists(report)
    assert report["failed"][0]["owner_repo"] == "acme/stalls"
    assert report["failed"][0]["reason"] == "fetch"  # a stall is tomorrow's business
    assert "gh timed out" in report["failed"][0]["error"]
    assert report["updated"] == [good.relative_to(tmp_path).as_posix()]
    assert parse_frontmatter(good.read_text())["stars"] == "42"
    assert parse_frontmatter(stalled.read_text())["stars"] == "1"  # untouched


def test_refresh_names_the_vault_when_the_rename_probe_cannot_be_read(
    tmp_path: Path, monkeypatch
) -> None:
    # Planning a rename asks whether the target slug is taken, and an iCloud vault can
    # refuse that read. Reported as the vault's failure, not as a `gh` payload defect —
    # the wording is what sends the reader to the right place, and the payload wording
    # is the one the run summary escalates on.
    # Patched at `lstat`, which is where `name_taken` asks: `Path.exists` swallows
    # every `OSError` from CPython 3.14 on, so patching it would pin a raise the
    # runtime cannot produce and this arm would be green and unreachable.
    old = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")
    taken = f"{canonical_slug('https://github.com/a2aproject/A2A')}.md"
    real_lstat = Path.lstat

    def refusing_lstat(self: Path, **kwargs: object) -> os.stat_result:
        if self.name == taken:
            raise PermissionError("Operation not permitted")
        return real_lstat(self, **kwargs)

    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )
    monkeypatch.setattr(Path, "lstat", refusing_lstat)

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["failed"] == 1
    assert report["failed"][0]["reason"] == "vault"  # not "payload", so no escalation
    assert "Operation not permitted" in report["failed"][0]["error"]
    assert old.exists()


def test_refresh_reports_a_rename_that_left_both_files(tmp_path: Path, monkeypatch) -> None:
    # The one failure that does not leave the note as it was: the new slug is written
    # and the old file survives. The `failed` entry has to say so, or it reads as
    # "nothing happened here" and a human never goes looking for the duplicate.
    old = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")

    def undeletable(self: Path, **kwargs: object) -> None:
        # `atomic_write_text` unlinks its temp file too, on the write paths that fail;
        # this run's write succeeds, so the only unlink reached is the old note's.
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )
    monkeypatch.setattr(Path, "unlink", undeletable)

    report = refresh(tmp_path, today=TODAY)

    new_path = tmp_path / "Repos" / f"{canonical_slug('https://github.com/a2aproject/A2A')}.md"
    assert old.exists() and new_path.exists()  # the state the report has to describe
    assert report["counts"] == {
        "total": 1,
        "updated": 0,
        "adopted": 0,
        "rename_collisions": 0,
        "failed": 1,
        "anomalies": 0,
    }
    error = report["failed"][0]["error"]
    assert new_path.relative_to(tmp_path).as_posix() in error
    assert "both now exist" in error


def test_refresh_reports_a_collision_even_when_the_rewrite_then_fails(
    tmp_path: Path, monkeypatch
) -> None:
    # The one list that is deliberately not exclusive with `failed`: a canonical slug
    # being taken is a finding about identity, and it holds whether or not the metadata
    # rewrite that follows lands. Moving the collision past the write — the natural
    # follow-on to `adopted` moving there — would leave the human never told that two
    # notes need merging.
    _write_note(tmp_path, "https://github.com/a2aproject/A2A", "a2aproject/A2A")
    collided = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")

    def unwritable(path: Path, content: str) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )
    monkeypatch.setattr(refresh_mod, "atomic_write_text", unwritable)

    report = refresh(tmp_path, today=TODAY)

    rel = collided.relative_to(tmp_path).as_posix()
    assert [c["path"] for c in report["rename_collisions"]] == [rel]
    assert rel in [f["path"] for f in report["failed"]]
    assert report["counts"]["updated"] == 0
    assert _counts_match_the_lists(report)


def test_refresh_escalates_a_note_with_no_line_to_rewrite(tmp_path: Path, monkeypatch) -> None:
    # A note missing a field the refresh rewrites fails identically every run — it is
    # the note's shape, not the weather — so it cannot be reported as one the next run
    # settles. The catalogue-wide form is a field added to the refresh without the
    # notes being migrated, and then this is every note at once.
    note = _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    note.write_text(
        "\n".join(
            line
            for line in note.read_text(encoding="utf-8").splitlines()
            if "homepage:" not in line
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (_meta(o, r), None))

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["failed"] == 1
    assert report["failed"][0]["reason"] == "note"  # not `write`, which promises a retry
    assert "homepage" in report["failed"][0]["error"]


def test_a_dry_run_surfaces_a_note_with_no_line_to_rewrite(tmp_path: Path, monkeypatch) -> None:
    # The preview an operator checks a `github_fields` addition against. If it skipped
    # building the content, it would report a clean full-catalogue update and the next
    # unattended run would fail every note — the one failure a preview exists to catch.
    note = _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    kept = note.read_text(encoding="utf-8")
    note.write_text(
        "\n".join(line for line in kept.splitlines() if "homepage:" not in line) + "\n",
        encoding="utf-8",
    )
    before = note.read_text(encoding="utf-8")
    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (_meta(o, r), None))

    report = refresh(tmp_path, today=TODAY, dry_run=True)

    assert report["counts"]["updated"] == 0
    assert report["failed"][0]["reason"] == "note"
    assert note.read_text(encoding="utf-8") == before  # a preview still writes nothing


def test_refresh_counts_a_rename_whose_old_file_vanished_as_healed(
    tmp_path: Path, monkeypatch
) -> None:
    # The lock is advisory, so the old note can be removed under the run — by iCloud,
    # or by a human in Obsidian. The rename completed; reporting it as a duplicate
    # would send someone to merge a note that is not there.
    old = _write_note(tmp_path, "https://github.com/google/A2A", "google/A2A")
    real_write = refresh_mod.atomic_write_text

    def write_then_vanish(path: Path, content: str) -> None:
        real_write(path, content)
        old.unlink(missing_ok=True)

    monkeypatch.setattr(
        refresh_mod, "gh_repo_view", lambda o, r: (_meta("a2aproject", "A2A"), None)
    )
    monkeypatch.setattr(refresh_mod, "atomic_write_text", write_then_vanish)

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"]["adopted"] == 1
    assert report["counts"]["failed"] == 0
    assert (
        tmp_path / "Repos" / f"{canonical_slug('https://github.com/a2aproject/A2A')}.md"
    ).exists()


def test_refresh_isolates_a_note_that_turns_unreadable_mid_pass(
    tmp_path: Path, monkeypatch
) -> None:
    # The note is read twice — once to learn its identity, once to rewrite it — and the
    # vault lock is advisory, so Obsidian or iCloud can change the file in between. The
    # second read is inside the per-note boundary, so this is one `failed` entry rather
    # than the end of the pass.
    note = _write_note(tmp_path, "https://github.com/acme/tool", "acme/tool")
    real_read_text = Path.read_text
    reads: list[Path] = []

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.parent.name == "Repos":
            reads.append(self)
            if len(reads) > 1:  # the rewrite's read, after the load's
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return real_read_text(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(refresh_mod, "gh_repo_view", lambda o, r: (_meta(o, r), None))
    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    report = refresh(tmp_path, today=TODAY)

    assert report["counts"] == {
        "total": 1,
        "updated": 0,
        "adopted": 0,
        "rename_collisions": 0,
        "failed": 1,
        "anomalies": 0,
    }
    assert report["failed"][0]["reason"] == "write"
    assert note.exists()


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
    assert report["failed"][0]["reason"] == "write"
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
