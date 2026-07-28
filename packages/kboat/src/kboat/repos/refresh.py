"""Refresh every repo note's GitHub-derived frontmatter.

Drain ingestion takes a repo snapshot once; this keeps it fresh. It re-fetches
every `Repos/*.md` note via `gh` (in parallel), rewrites only the
GitHub-derived fields plus `status` and `refreshed_date`, and preserves the
judgement layer (role/domain/summary) and the human-edited `## Notes` body.

It never deletes a note: a renamed or deleted repo (a `gh` error, or a resolved
`owner/repo` that differs from the note) is reported for the human to act on,
not patched. The report is JSON on stdout for the `kboat-repos` skill to relay.

An applying run holds the vault lock for its whole pass and reports a `locked`
record rather than racing a run that already has it; a `--dry-run` writes nothing,
so it takes no lock.

The hold spans the `gh` fetch as well as the rewrites, and has to: the identity each
note is fetched under is read before the fetch and rewritten after it, so this pass
is one read-modify-write and narrowing the hold to the writes would open the
lost-update window the lock is here to close.

It is also the longest hold in the system, and the one `kboat.lock`'s wait is *not*
sized for: that wait covers overlapping a single note write, so another writer meeting
this pass waits its few seconds and is then refused. What that costs is the *other*
run's work — a feed entry deferred to the next gather, a phase left for tomorrow —
which is the trade the wide hold is worth, since narrowing it would lose an update
rather than defer one.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from kboat.cli import (
    add_today_argument,
    add_vault_argument,
    emit_lock_unavailable,
    emit_locked,
    vault_path,
)
from kboat.frontmatter import FrontmatterError, parse_frontmatter
from kboat.frontmatter import set_fields as _set_rendered_fields
from kboat.io_utils import atomic_write_text
from kboat.lock import VaultLockedError, VaultLockUnavailableError, vault_lock
from kboat.schema import DIR_BY_TYPE, REPO
from kboat.write import render_field

from .gather import PayloadError, gh_repo_view, github_fields, resolved_identity
from .identity import canonical_slug, canonical_url, parse_repo

MAX_WORKERS = 10


# The closed set of ways one note drops out of a pass. Typed, not free strings,
# because `reason` is what the run branches on to escalate: a misspelling or an
# unannounced value would otherwise type-check clean and quietly stop the
# escalation firing. Every site that sets one goes through `_fetched` or
# `_failure`, so the annotation is what the value is checked against rather than
# decoration. Two of the five need a human and no later run clears them —
# `payload` (the mapping) and `note` (the note's own shape).
Reason = Literal["fetch", "payload", "vault", "note", "write"]


def _fetched(note: dict, *, meta: dict | None, error: str | None, reason: Reason | None) -> dict:
    """One note's fetch result, with its failure class named where it is decided.

    `reason` is `None` for the fetch that worked: the set enumerates the ways a note
    drops out, so tagging a healthy note with one would leave anything that later
    read it without checking `meta` calling every good note a failure.
    """
    return {**note, "meta": meta, "error": error, "reason": reason}


def _failure(note_rel: str, owner_repo: str, *, reason: Reason, error: str) -> dict[str, str]:
    """One `failed` entry: the note, why it dropped out, and the detail.

    The run branches on `reason`, never on `error`, which carries `gh`'s stderr and
    an exception's text — neither of which this side writes. `payload` is the one
    no later run clears; the others are settled by trying again.
    """
    return {"path": note_rel, "owner_repo": owner_repo, "reason": reason, "error": error}


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
        # `UnicodeDecodeError` alongside the other two: it is a `ValueError`, so a
        # note that is not UTF-8 would otherwise escape every boundary in this pass
        # and take the whole catalogue's refresh with it.
        except (FrontmatterError, OSError, UnicodeDecodeError) as exc:
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
    """One note's `gh` fetch, as this note's result rather than as an exception.

    Runs in a worker thread, and `Executor.map` re-raises in the parent — so a
    `gh` that times out or is missing from `PATH` would take the whole pass down
    with it, report included. Caught, it lands in the same shape a non-zero `gh`
    does, tagged with the `reason` that says which of the two it was.
    """
    try:
        meta, err = gh_repo_view(note["owner"], note["repo"])
    except PayloadError as exc:
        return _fetched(note, meta=None, error=str(exc), reason="payload")
    except Exception as exc:  # noqa: BLE001
        return _fetched(note, meta=None, error=f"{type(exc).__name__}: {exc}", reason="fetch")
    if meta is None:
        # `gh` did not answer. Its stderr can be empty — a `gh` the OOM killer took,
        # one that wrote its diagnostic to stdout — so the class comes from whether
        # there is a payload, never from whether there is text to show for it.
        return _fetched(note, meta=None, error=err, reason="fetch")
    return _fetched(note, meta=meta, error=err, reason=None)


@dataclass(frozen=True)
class _NotePlan:
    """Everything one note's refresh will do, decided before any of it is applied.

    Deciding it in one piece is what keeps the run's accounting honest. The payload
    mapping can raise (`gather`'s `defect-payload` class), and a note that fails
    there is in `failed` and neither `updated` nor `adopted`, both of which are
    appended only once the write has landed.

    `rename_collisions` is not exclusive with `failed`, and deliberately: it is a
    finding about identity — the canonical slug is taken — which holds whichever
    way the rewrite that follows it goes. It already co-occurs with `updated` for
    the note it refreshes in place.
    """

    path: Path
    rel: str
    target: Path
    rel_target: str
    now: str
    updates: dict[str, object]
    adopt: bool
    renaming: bool
    collides: bool

    @property
    def slug(self) -> str:
        """The slug this plan writes under — read off `target`, never stored beside it.

        The reservation set and the written path have to be the same string, and
        two fields holding it would let a later change move one and not the other.
        """
        return self.target.stem


def _plan(note: dict, *, repos_dir: Path, vault: Path, today: date, claimed: set[str]) -> _NotePlan:
    """Read a fetched note's payload into the update it implies.

    Pure computation over `gh`'s answer and the slugs already claimed this run:
    it reads the filesystem only to test whether a rename's target is taken, and
    writes nothing.
    """
    meta, path = note["meta"], note["path"]
    res_owner, res_repo = resolved_identity(meta)
    res_owner, res_repo = res_owner or note["owner"], res_repo or note["repo"]
    # A repo's identity can move (rename/transfer/case); the note must follow it
    # so de-dup and refresh stay keyed off the live repo. Adopt the resolved
    # name, renaming the file when its slug changes.
    resolved_url = canonical_url(res_owner, res_repo)
    new_slug = canonical_slug(resolved_url) or path.stem
    adopt = resolved_url != canonical_url(note["owner"], note["repo"])
    renaming = adopt and new_slug != path.stem
    target = repos_dir / f"{new_slug}.md"
    # The canonical slug is already taken (by an existing note, or by another
    # rename this run) — refresh metadata in place but don't adopt the identity
    # or move; a human merges the two.
    collides = renaming and (target.exists() or new_slug in claimed)
    if collides:
        adopt = renaming = False

    updates = github_fields(meta, today=today)
    updates["refreshed_date"] = today.isoformat()
    if adopt:
        updates["url"] = resolved_url
        updates["title"] = f"{res_owner}/{res_repo}"
    return _NotePlan(
        path=path,
        rel=note["rel"],
        target=target,
        # Derived from `target`, so a report path cannot name a different
        # directory from the one the write actually went to.
        rel_target=target.relative_to(vault).as_posix(),
        now=f"{res_owner}/{res_repo}",
        updates=updates,
        adopt=adopt,
        renaming=renaming,
        collides=collides,
    )


def _apply(plan: _NotePlan, *, dry_run: bool) -> str:
    """Write the planned update, returning the path the report names it under.

    A dry run writes nothing, but it still builds the content, so that a note with
    no line for a field this pass rewrites fails the preview as it would fail the
    run. A preview is what an operator checks a `github_fields` addition against,
    and one that cannot show the failure that addition causes is worse than none.
    """
    content = set_fields(plan.path.read_text(encoding="utf-8"), plan.updates)
    if dry_run:
        return plan.rel_target if plan.renaming else plan.rel
    if plan.renaming:
        atomic_write_text(plan.target, content)
        try:
            # `missing_ok`: the old note going away under the run is the rename
            # completing, not failing. The vault lock is advisory, so Obsidian or
            # iCloud can be what removed it, and reporting that as a duplicate would
            # send a human to merge a note that is not there.
            plan.path.unlink(missing_ok=True)
        except OSError as exc:
            # The one failure that does not leave the note as it was: the new slug
            # is written and the old file is still there, so the vault now holds
            # both. The `failed` entry has to say so, or it reads as "nothing
            # happened here" and the duplicate surfaces only when a later run
            # collides on it and asks a human to merge two notes it cannot explain.
            raise OSError(
                f"rewritten as {plan.rel_target}, but the old file could not be "
                f"removed, so both now exist: {exc}"
            ) from exc
        return plan.rel_target
    atomic_write_text(plan.path, content)
    return plan.rel


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
        # `None` is the whole of the no-payload case: what makes a payload usable is
        # `gh_repo_view`'s to decide, and it decides once, for both callers, by
        # raising rather than returning. A second opinion here would be a second
        # place to keep in step — which is how this pair drifted apart before.
        if r["meta"] is None:
            failed.append(_failure(rel, was, reason=r["reason"], error=r["error"]))
            continue
        # The per-note boundary over the payload mapping, blind for the reason
        # `gather`'s is: `gh repo view --json` is a typed schema, so what can raise
        # in here is a breaking `gh` change or an anomalous response, and neither
        # is worth the rest of the catalogue. The note keeps its existing content
        # and is re-fetched by the next run.
        try:
            plan = _plan(r, repos_dir=repos_dir, vault=vault, today=today, claimed=claimed)
        except OSError as exc:
            # `_plan` touches the filesystem once, to see whether a rename's target
            # is taken, and an iCloud vault can refuse that read. Calling it a
            # payload defect would send the reader to `gh`'s release notes for a
            # problem that is in the vault — and would escalate a run that only
            # needs the vault back.
            failed.append(_failure(rel, was, reason="vault", error=str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001
            failed.append(
                _failure(rel, was, reason="payload", error=f"{type(exc).__name__}: {exc}")
            )
            continue

        if plan.collides:
            rename_collisions.append(
                {"path": rel, "was": was, "now": plan.now, "conflict": plan.rel_target}
            )

        try:
            written = _apply(plan, dry_run=dry_run)
        except FrontmatterError as exc:
            # The note has no line to rewrite for a field this pass rewrites. That is
            # the note's own shape, not the weather: it fails identically every run,
            # and if it followed an addition to `github_fields` it fails for the whole
            # catalogue at once. Sorted apart from `write` so the run escalates it
            # rather than promising a next run that cannot help.
            failed.append(_failure(rel, was, reason="note", error=str(exc)))
            continue
        # `UnicodeDecodeError` for the same reason as the load above: `_apply` reads
        # the note again, and the file can have changed under the run.
        except (OSError, UnicodeDecodeError) as exc:
            failed.append(_failure(rel, was, reason="write", error=str(exc)))
            continue
        updated.append(written)
        if plan.renaming:
            # Claimed once the slug is actually taken (in a dry run, once it would
            # have been), for the same reason `adopted` is: a rename whose write
            # failed left the slug free, and reserving it anyway would report the
            # next note as colliding with a file that was never written.
            claimed.add(plan.slug)
        if plan.adopt:
            # Reported only once the note is actually written (or, in a dry run,
            # once it would have been): `adopted` is the set of renames healed,
            # and a rewrite that failed healed nothing.
            adopted.append({"from": rel, "to": plan.rel_target, "was": was, "now": plan.now})

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

    today = date.fromisoformat(args.today)
    try:
        with ExitStack() as stack:
            if not args.dry_run:
                stack.enter_context(vault_lock(vault))
            report = refresh(vault, today=today, dry_run=args.dry_run)
    except VaultLockedError as exc:
        return emit_locked(exc)
    except VaultLockUnavailableError as exc:
        return emit_lock_unavailable(exc)

    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if report.get("error") else 0
