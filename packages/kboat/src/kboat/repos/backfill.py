"""`kboat-repos backfill-readme`: give every repo note that lacks one `readme: unknown`.

Every note written before the `readme` field existed was classified with or
without its README, and which it was is not recoverable, so the only value that
does not overclaim is `unknown` (`kboat-notes` "Repo note"). This writes that and
nothing else: a note already carrying a `readme` line is left as it is, whatever
it holds, so a mark `kboat-repos write` set is never downgraded and a malformed one
stays for `kboat-validate` to report.

The note is re-assembled the way every update write assembles it — the new entry
rendered, every other entry put back verbatim — but not through `upsert`, which
would stamp `refreshed_date` on a note whose metadata nobody refreshed.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path

from kboat.cli import (
    add_vault_argument,
    emit_lock_unavailable,
    emit_locked,
    scan_required_dir,
    vault_path,
)
from kboat.frontmatter import (
    NOTE_READ_ERRORS,
    body_after_frontmatter,
    names_key,
    parse_entries,
    parse_frontmatter,
    repeated_keys,
)
from kboat.io_utils import atomic_write_text
from kboat.lock import VaultLockedError, VaultLockUnavailableError, vault_lock
from kboat.schema import DIR_BY_TYPE, REPO, ReadmeMark
from kboat.write import build_note

_FIELD = "readme"


def backfill(vault: Path, *, apply: bool) -> tuple[dict, bool]:
    """The backfill report, and whether `Repos/` itself could not be read.

    A note that cannot be read as a repo note, or an evicted one, is an `anomalies`
    entry and is not marked: it is outside `total`, and a re-run once it is readable
    marks it, since a marked note is skipped.
    """
    found, anomalies, unread = scan_required_dir(vault, DIR_BY_TYPE["repo"])
    total = 0
    marked: list[str] = []
    failed: list[dict[str, str]] = []
    for path in found:
        rel = path.relative_to(vault).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            entries = parse_entries(text)
            fm = parse_frontmatter(text)
        except NOTE_READ_ERRORS as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        if fm.get("type") != "repo":
            anomalies.append({"path": rel, "error": "frontmatter 'type' is not 'repo'"})
            continue
        total += 1
        # Asked of what each entry is about, not of what decoded: a `readme` line
        # the reader cannot model is still the note's mark, and adding a second
        # one would leave the note holding two.
        if any(names_key(entry.lines[0], _FIELD) for entry in entries):
            continue
        # Re-assembling the note keeps only the last line of a repeated key, which
        # would delete the one a human editing the note sees, and which of the two
        # was meant is not in the note — so the note is left for a human first.
        repeated = repeated_keys(text)
        if repeated:
            names = ", ".join(f"'{key}' on {count} lines" for key, count in repeated.items())
            failed.append({"path": rel, "error": f"note names {names}"})
            continue
        if apply:
            content = build_note(
                REPO, {_FIELD: ReadmeMark.UNKNOWN}, body_after_frontmatter(text), entries
            )
            try:
                atomic_write_text(path, content)
            except OSError as exc:
                failed.append({"path": rel, "error": str(exc)})
                continue
        marked.append(rel)
    report = {
        "dry_run": not apply,
        "counts": {
            "total": total,
            "marked": len(marked),
            "failed": len(failed),
            "anomalies": len(anomalies),
        },
        "marked": marked,
        "failed": failed,
        "anomalies": anomalies,
    }
    return report, unread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-repos backfill-readme",
        description="Write `readme: unknown` on every Repos/*.md note that has no readme line.",
    )
    add_vault_argument(parser)
    mode = parser.add_mutually_exclusive_group(required=True)
    # Required and exclusive, as for `kboat-note migrate-slugs`: a bare invocation
    # must not be the one that writes.
    mode.add_argument("--dry-run", action="store_true", help="Report the notes only.")
    mode.add_argument("--apply", action="store_true", help="Write the mark.")
    args = parser.parse_args(argv)
    vault = vault_path(parser, args)

    try:
        with vault_lock(vault) if args.apply else nullcontext():
            report, unread = backfill(vault, apply=args.apply)
    except VaultLockedError as exc:
        return emit_locked(exc)
    except VaultLockUnavailableError as exc:
        return emit_lock_unavailable(exc)

    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if unread or report["failed"] else 0
