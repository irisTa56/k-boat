"""CLI entry point: `kboat-note <subcommand>`.

- `write --type {source,kindle,repo,feed}` — create or update a vault note from a
  `{slug, fields, body?}` JSON record on stdin, schema-driven via `kboat.write`
  (`upsert`): frontmatter order, YAML quoting, the always-present fields, body
  preservation, slug de-dup, and the `added_date`/`refreshed_date` stamps are all
  guaranteed by the package, so the agent never hand-writes frontmatter. Prints
  the result (`{status, slug, path}` — `created`, `updated`, or an `evicted`
  refusal — or a `collision`/`slug_mismatch`/`repeated_key`) as JSON.
  The write is held under the vault lock, so a refused vault prints a `locked`
  record and exits non-zero instead of racing the run that holds it.
- `slug <url>` — the slug oracle: `{url, canonical_url, slug}` for one URL. Every
  skill that names a note asks this rather than hashing a URL itself, so the name
  a note is written under is the one the write contract verifies. Read-only, and
  it touches no vault.
- `migrate-slugs --dry-run|--apply` — bring the vault's URL-named notes to that
  same slug. Reports every stale name, and under `--apply` renames it. Mutating,
  so `--apply` holds the vault lock; `--dry-run` reads and takes none.
- `list --type <t> [--slug <slug>] [--field <name>]... [--flagged <name>]...` —
  one note folder's frontmatter, or one note's by slug, as `{notes, anomalies,
  counts}` JSON (`kboat.note.listing`), so a skill reads the vault without
  globbing or parsing frontmatter itself. Read-only, and it takes no lock. A
  folder it could not read is an `anomalies` entry under its own name and exit 1,
  with the report still printed (`kboat-vault-conventions` "Vault preconditions").
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
from collections import Counter
from contextlib import nullcontext

from kboat.canonical import canonical_url
from kboat.cli import (
    add_today_argument,
    add_vault_argument,
    emit_lock_unavailable,
    emit_locked,
    require_readable_payload,
    run_write,
    vault_path,
)
from kboat.lock import VaultLockedError, VaultLockUnavailableError, vault_lock
from kboat.naming import note_slug
from kboat.note.listing import list_notes
from kboat.note.migrate import UNREADABLE_DIR, migrate
from kboat.schema import BY_TYPE, DIR_BY_TYPE, Kind
from kboat.write import upsert


def _write(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-note write",
        description="Create or update a vault note from a JSON record on stdin.",
    )
    parser.add_argument("--type", required=True, choices=sorted(BY_TYPE))
    add_vault_argument(parser)
    add_today_argument(parser)
    args = parser.parse_args(argv)
    vault = vault_path(parser, args)

    def write(record: dict) -> dict[str, object]:
        # The record is checked before the lock is taken: one this writer cannot
        # read is the agent's to fix, and it never reaches the vault.
        require_readable_payload(record)
        with vault_lock(vault):
            return upsert(BY_TYPE[args.type], vault, record, today=args.today)

    return run_write(write)


def _slug(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-note slug",
        description="Print the canonical URL and vault slug for one URL.",
    )
    parser.add_argument("url", help="The note's identity URL, in any form.")
    args = parser.parse_args(argv)
    try:
        record = {
            "url": args.url,
            "canonical_url": str(canonical_url(args.url)),
            "slug": note_slug(args.url),
        }
    except ValueError as exc:
        # The skills call this on queue captures, which are untrusted page text,
        # so a string no parser can take is an input to report rather than a
        # traceback. Exit 2 is the argument-to-fix code the writer also uses.
        sys.stderr.write(f"not a usable URL: {args.url!r} ({exc})\n")
        return 2
    json.dump(record, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def _migrate_slugs(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-note migrate-slugs",
        description="Rename every URL-named vault note to the slug its url names.",
    )
    add_vault_argument(parser)
    mode = parser.add_mutually_exclusive_group(required=True)
    # Required and exclusive: there is no sensible default between reporting and
    # renaming, and a bare invocation must not be the one that moves files.
    mode.add_argument("--dry-run", action="store_true", help="Report the stale names only.")
    mode.add_argument("--apply", action="store_true", help="Rename them.")
    args = parser.parse_args(argv)
    vault = vault_path(parser, args)
    # A vault root is a precondition, not something a tool creates. Reported in
    # both modes, because the dry run is the report a human approves the apply
    # from: a mis-typed `--vault` that scanned nothing would read as a vault
    # already canonical. Three answers from one `stat` rather than `is_dir()`,
    # which swallows a refusal on 3.14 and raises on 3.13 — either way reporting a
    # vault that is sitting there as one that is not, with nothing naming the
    # readability that is actually wrong.
    try:
        mode = vault.stat().st_mode
    except FileNotFoundError, NotADirectoryError:
        sys.stderr.write(f"no vault at {vault}\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"vault at {vault} could not be read: {exc}\n")
        return 1
    if not stat.S_ISDIR(mode):
        sys.stderr.write(f"vault at {vault} is not a directory\n")
        return 1

    try:
        # The lock covers the scan as well as the renames: a plan computed before
        # another run's writes would move notes it no longer describes.
        with vault_lock(vault) if args.apply else nullcontext():
            report = migrate(vault, apply=args.apply)
    except VaultLockedError as exc:
        return emit_locked(exc)
    except VaultLockUnavailableError as exc:
        return emit_lock_unavailable(exc)
    json.dump(report.to_json(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if report.unresolved:
        # The re-run only helps where this pass moved a note another was waiting
        # on. A dry run moved nothing, and two notes each holding the other's
        # name never clear each other however often the pass repeats.
        again = (
            " — one of them may be waiting on a note this pass renamed, so re-run"
            if report.counts()["renamed"]
            else ""
        )
        sys.stderr.write(f"{report.unresolved} note(s) not renamed{again}: see the rows' detail\n")
    if report.skipped:
        # Said on stderr as well as in the report, because a dry run is read for
        # its exit code before its JSON, and a skipped note is one the oracle
        # cannot answer for — silence there would pass a vault off as canonical
        # when part of it was never examined. Broken down by reason, since they
        # ask for different answers: a note with no `url` at all has no name to
        # check against, while a `url` nothing can read is a name to repair. A skipped note is not
        # an exit code: nothing about one stops the rest of the pass. A skipped
        # directory is, below.
        by_reason = Counter(s.reason.split(":")[0] for s in report.skipped)
        # A directory entry is counted apart from the notes: one of them stands for
        # however many notes went unseen, so folding it in would put a fixed "1"
        # where the true number is unknown — on the report an `--apply` is approved
        # from, and for the one skip whose remedy is the vault rather than the note.
        dirs = by_reason.pop(UNREADABLE_DIR, 0)
        parts = []
        if by_reason:
            named = ", ".join(f"{n} {reason}" for reason, n in sorted(by_reason.items()))
            parts.append(f"{sum(by_reason.values())} note(s) skipped ({named})")
        if dirs:
            parts.append(f"{dirs} note director(ies) could not be read, contents unseen")
        sys.stderr.write("; ".join(parts) + "\n")
    # A note directory it could not read is an exit of its own even with every row
    # renamed: the folders scanned are in the vault's required set, and a dry run
    # read for its exit code would otherwise pass off an unsynced vault as a
    # canonical one (`kboat-vault-conventions` "Vault preconditions").
    return 1 if report.unresolved or report.counts()["unreadable_dirs"] else 0


def _list(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-note list",
        description="Print one note folder's frontmatter, or one note's by slug, as JSON.",
    )
    parser.add_argument("--type", required=True, choices=sorted(DIR_BY_TYPE))
    parser.add_argument("--slug", help="Report only the note at this slug.")
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME",
        help="Print only this frontmatter field (repeatable; default: every field).",
    )
    parser.add_argument(
        "--flagged",
        action="append",
        default=[],
        metavar="NAME",
        help="Keep only the notes whose boolean field NAME is true (repeatable).",
    )
    add_vault_argument(parser)
    args = parser.parse_args(argv)
    vault = vault_path(parser, args)
    # Checked against the type's schema: a misspelt name would otherwise print
    # every note with no fields, or keep none, and either reads as an answer.
    schema = BY_TYPE[args.type]
    for name in args.field:
        if schema.get(name) is None:
            parser.error(f"--field {name!r} is not a {args.type} field")
    for name in args.flagged:
        field = schema.get(name)
        if field is None or field.kind is not Kind.BOOL:
            parser.error(f"--flagged {name!r} is not a {args.type} boolean field")
    report, unread = list_notes(
        vault, args.type, slug=args.slug, fields=args.field, flagged=args.flagged
    )
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    # A note it could not read is not an exit: the rest of the answer stands, and
    # the entry says which note is missing from it.
    return 1 if unread else 0


_COMMANDS = {
    "write": _write,
    "slug": _slug,
    "migrate-slugs": _migrate_slugs,
    "list": _list,
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        sys.stderr.write(f"usage: kboat-note {{{','.join(_COMMANDS)}}} ...\n")
        return 0 if args[:1] in ([], ["-h"], ["--help"]) else 2
    command, rest = args[0], args[1:]
    handler = _COMMANDS.get(command)
    if handler is None:
        sys.stderr.write(
            f"unknown subcommand: {command!r} (expected one of {', '.join(_COMMANDS)})\n"
        )
        return 2
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
