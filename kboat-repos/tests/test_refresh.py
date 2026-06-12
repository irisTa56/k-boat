"""Tests for `refresh` — metadata update, judgement/body preservation, and the
adopt-rename of renamed/transferred/case-changed repos. `gh` is monkeypatched."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import kboat_repos.refresh as refresh_mod
from kboat_repos.identity import canonical_slug
from kboat_repos.notes import build_repo_note, parse_frontmatter
from kboat_repos.refresh import refresh

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
        "added_date": "2024-01-01",
        "refreshed_date": "2024-01-01",
    }


def _write_note(vault: Path, url: str, title: str, body: str = "keep me") -> Path:
    slug = canonical_slug(url)
    assert slug
    path = vault / "Repos" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_repo_note(_note_fields(url, title), notes_body=body))
    return path


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
