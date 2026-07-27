"""Refresh every repo note's GitHub-derived frontmatter.

Drain ingestion takes a repo snapshot once; this keeps it fresh. It re-fetches
every `Repos/*.md` note via `gh` (in parallel), rewrites only the
GitHub-derived fields plus `status` and `refreshed_date`, and preserves the
judgement layer (role/domain/summary) and the human-edited `## Notes` body.

It never deletes a note: a renamed or deleted repo (a `gh` error, or a resolved
`owner/repo` that differs from the note) is reported for the human to act on,
not patched. The report is JSON on stdout for the `kboat-repos` skill to relay.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from kboat.cli import add_today_argument, add_vault_argument, vault_path
from kboat.frontmatter import FrontmatterError, parse_frontmatter
from kboat.frontmatter import set_fields as _set_rendered_fields
from kboat.io_utils import atomic_write_text
from kboat.schema import DIR_BY_TYPE, REPO
from kboat.write import render_field

from .gather import gh_repo_view, github_fields, resolved_identity
from .identity import canonical_slug, canonical_url, parse_repo

MAX_WORKERS = 10


def set_fields(text: str, updates: Mapping[str, object]) -> str:
    """Rewrite the named top-level frontmatter lines in place.

    Each key in `updates` must already exist as a top-level line (refresh targets
    always-present GitHub-derived fields); a missing key is a `FrontmatterError`,
    not a silent insert. The field order, every other field, and the body survive.

    An in-place line rewrite rather than a re-write of the whole note, which is
    what makes the judgement layer and the body untouchable here. It works
    because every repo list (`language`, `topics`, `domain`) is inline
    (`topics: [a, b]`), so each top-level field occupies exactly one line.
    """
    rendered = {key: render_field(REPO.get(key), value) for key, value in updates.items()}
    return _set_rendered_fields(text, rendered)


def _load_repo_notes(repos_dir: Path, vault: Path) -> tuple[list[dict], list[dict[str, str]]]:
    notes: list[dict] = []
    anomalies: list[dict[str, str]] = []
    for path in sorted(repos_dir.glob("*.md")):
        rel = path.relative_to(vault).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
        except (FrontmatterError, OSError) as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        if fm.get("type") != "repo":
            anomalies.append({"path": rel, "error": "frontmatter 'type' is not 'repo'"})
            continue
        url = fm.get("url")
        owner, repo = parse_repo(url) if isinstance(url, str) else (None, None)
        if not owner or not repo:
            anomalies.append({"path": rel, "error": f"unparseable github url: {url!r}"})
            continue
        notes.append({"path": path, "rel": rel, "owner": owner, "repo": repo})
    return notes, anomalies


def _fetch(note: dict) -> dict:
    meta, err = gh_repo_view(note["owner"], note["repo"])
    return {**note, "meta": meta, "error": err}


def refresh(
    vault: Path, *, today: date, max_workers: int = MAX_WORKERS, dry_run: bool = False
) -> dict:
    repos_dir = vault / DIR_BY_TYPE["repo"]
    if not repos_dir.is_dir():
        return {"error": f"no {DIR_BY_TYPE['repo']}/ directory under vault: {repos_dir}"}

    notes, anomalies = _load_repo_notes(repos_dir, vault)
    today_iso = today.isoformat()
    updated: list[str] = []
    adopted: list[dict[str, str]] = []
    rename_collisions: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    # Slugs a rename has claimed this run, so two repos resolving to the same new
    # slug collide consistently in dry-run too (not just once a file is written).
    claimed: set[str] = set()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_fetch, notes))

    for r in results:
        rel = r["rel"]
        was = f"{r['owner']}/{r['repo']}"
        if r["meta"] is None:
            failed.append({"path": rel, "owner_repo": was, "error": r["error"] or "no metadata"})
            continue
        meta = r["meta"]
        path: Path = r["path"]
        res_owner, res_repo = resolved_identity(meta)
        res_owner, res_repo = res_owner or r["owner"], res_repo or r["repo"]
        now = f"{res_owner}/{res_repo}"
        # A repo's identity can move (rename/transfer/case); the note must follow
        # it so de-dup and refresh stay keyed off the live repo. Adopt the
        # resolved name, renaming the file when its slug changes.
        resolved_url = canonical_url(res_owner, res_repo)
        new_slug = canonical_slug(resolved_url) or path.stem
        adopt = resolved_url != canonical_url(r["owner"], r["repo"])
        renaming = adopt and new_slug != path.stem
        target = repos_dir / f"{new_slug}.md"
        # Derived from `target`, so a report path cannot name a different
        # directory from the one the write actually went to.
        rel_target = target.relative_to(vault).as_posix()

        updates = github_fields(meta, today=today)
        updates["refreshed_date"] = today_iso

        if renaming and (target.exists() or new_slug in claimed):
            # The canonical slug is already taken (by an existing note, or by
            # another rename this run) — refresh metadata in place but don't adopt
            # the identity or move; a human merges the two.
            rename_collisions.append({"path": rel, "was": was, "now": now, "conflict": rel_target})
            adopt = renaming = False

        if renaming:
            claimed.add(new_slug)
        if adopt:
            updates["url"] = resolved_url
            updates["title"] = now
            adopted.append({"from": rel, "to": rel_target, "was": was, "now": now})

        if dry_run:
            updated.append(rel_target if renaming else rel)
            continue
        try:
            content = set_fields(path.read_text(encoding="utf-8"), updates)
            if renaming:
                atomic_write_text(target, content)
                path.unlink()
                updated.append(rel_target)
            else:
                atomic_write_text(path, content)
                updated.append(rel)
        except (FrontmatterError, OSError) as exc:
            failed.append({"path": rel, "owner_repo": was, "error": f"write failed: {exc}"})

    return {
        "today": today_iso,
        "vault": str(vault),
        "dry_run": dry_run,
        "counts": {
            "total": len(notes),
            "updated": len(updated),
            "adopted": len(adopted),
            "rename_collisions": len(rename_collisions),
            "failed": len(failed),
            "anomalies": len(anomalies),
        },
        "updated": updated,
        "adopted": adopted,
        "rename_collisions": rename_collisions,
        "failed": failed,
        "anomalies": anomalies,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-repos refresh",
        description="Re-fetch GitHub metadata for every Repos/*.md note and recompute status.",
    )
    add_vault_argument(parser)
    add_today_argument(parser)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and report without writing.")
    args = parser.parse_args(argv)

    vault = vault_path(parser, args)

    report = refresh(vault, today=date.fromisoformat(args.today), dry_run=args.dry_run)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if report.get("error") else 0
