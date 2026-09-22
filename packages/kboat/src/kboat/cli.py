"""Shared plumbing for the `kboat-*` CLIs.

Two layers, because the sharing is at two scales. Every `kboat-*` entry point
that reaches the vault takes the same `--vault`, and every one that needs a date
takes the same `--today` — so a flag means one thing across this package's
surface rather than whatever each `main` re-declared. `--today` reaches beyond
this package: a member CLI that stamps a note takes the flag from here, because
a date reaching the same writer has to have been through the same validation
whatever CLI it arrived at. (`--vault` does not — a member resolves the vault
its own way.) The report-shaped CLIs that read a folder the vault is required to
hold share `scan_required_dir` for the same reason: the entry standing for a folder
they could not read, and the exit it owes, mean one thing whichever CLI reports it.

On top of that, `kboat-note write` and `kboat-repos write` are two contracts over
one writer and share the shape of their whole transaction: one JSON record on
stdin, results on stdout and diagnostics on stderr, one mapping from outcome to
exit code. What each writer keeps is its own: the record shape it accepts, the
diagnostics for a record it refuses, and any key it adds to the result.

`kboat-bookmarklet` is the one CLI outside this: its `--vault` is the vault's
*name*, for the Obsidian URI, not a path to read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from kboat.frontmatter import NOTE_READ_ERRORS
from kboat.io_utils import list_note_dir, unread_dir
from kboat.lock import VaultLockedError, VaultLockUnavailableError
from kboat.write import WROTE_A_NOTE, BadInputError, WriteStatus

# The `error` of the anomaly a scan files under an evicted note's placeholder path.
EVICTED_NOTE = "iCloud placeholder: the note is evicted, so this pass cannot read it"


def _iso_date(value: str) -> str:
    """`value` as `YYYY-MM-DD`, or a usage error.

    Returned in canonical form rather than as given: `fromisoformat` also reads
    the basic (`20260606`) and week-date (`2026-W23-1`) forms, and a note writer
    stamps the value verbatim, where only `YYYY-MM-DD` is a date.
    """
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"must be YYYY-MM-DD, got {value!r}") from e


def add_vault_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--vault",
        default=os.environ.get("OBSIDIAN_VAULT_PATH"),
        help="Obsidian vault root (defaults to $OBSIDIAN_VAULT_PATH).",
    )


def add_today_argument(parser: argparse.ArgumentParser, *, hidden: bool = False) -> None:
    """Add `--today`, validated and canonicalised by argparse itself.

    A caller that wants a `date` reads `date.fromisoformat(args.today)` with no
    guard of its own: by then the value has already been through `_iso_date`.

    `hidden` keeps the flag out of `--help` for a CLI where it is a test hook
    rather than part of the documented surface. That is a presentational choice
    each CLI makes; the value still reaches a note through the same writer, so
    the validation is not one of them.
    """
    parser.add_argument(
        "--today",
        type=_iso_date,
        # The reader's *local* calendar day: a note stamped just before midnight JST
        # belongs to the day the reader had, so `datetime.now(UTC).date()` is wrong
        # here however well it satisfies DTZ011.
        default=datetime.now().astimezone().date().isoformat(),
        help=argparse.SUPPRESS
        if hidden
        else "Override today's date (YYYY-MM-DD); for testing and reproducibility.",
    )


def vault_path(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Path:
    """The vault root, or a usage error (exit 2) when neither flag nor env gives one."""
    if not args.vault:
        parser.error("no vault: pass --vault or set OBSIDIAN_VAULT_PATH")
    return Path(args.vault).expanduser()


def scan_required_dir(vault: Path, folder: str) -> tuple[list[Path], list[dict[str, str]], bool]:
    """A required folder's notes, the `{path, error}` anomalies for what they leave out,
    and whether the folder itself could not be read.

    For the report-shaped CLIs that read a folder in the vault's required set, which
    all carry the same `anomalies` entry and all owe the same exit
    (`kboat-vault-conventions` "Vault preconditions"): a folder that is absent, not
    a directory, or refused is one entry under the folder's own name and a `True`
    the caller turns into exit 1, and each note iCloud evicted is an entry under
    its placeholder's path. Neither is ever an empty folder: the first is a vault
    that did not sync or cannot be read, and the second is a note this pass was
    never shown.
    """
    try:
        found, placeholders = list_note_dir(vault / folder, required=True)
    except OSError as exc:
        return [], [{"path": folder, "error": unread_dir(exc)}], True
    evicted = [
        {"path": p.relative_to(vault).as_posix(), "error": EVICTED_NOTE} for p in placeholders
    ]
    return found, evicted, False


def require_readable_payload(record: dict) -> None:
    """Raise `BadInputError` unless the record's content keys are shapes the writer reads.

    `upsert` reads a `fields` it cannot map as no fields and a `body` it cannot
    read as no body, so on a create either would land an empty note and report
    it as written — and for a `verbatim` schema the body is the whole point of
    the write. A record that says something the writer cannot read is the
    agent's to fix, not the vault's to absorb.
    """
    if not isinstance(record.get("fields", {}), dict):
        raise BadInputError("record 'fields' must be a JSON object")
    if not isinstance(record.get("body", ""), str):
        raise BadInputError("record 'body' must be a string")


def emit_locked(exc: VaultLockedError) -> int:
    """Print the refusal for a vault another process holds, and return the exit code.

    Stdout carries the machine-readable `{status, holder}` record and stderr the
    line naming the holder, like every other diagnostic. The record has the same
    shape whichever CLI was refused, because the caller's move is the same one:
    nothing was written, so re-run once the holding run finishes.
    """
    sys.stderr.write(f"{exc}\n")
    record = {"status": WriteStatus.LOCKED, "holder": exc.holder}
    json.dump(record, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1


def emit_lock_unavailable(exc: VaultLockUnavailableError) -> int:
    """Print the failure for a vault lock that could not be taken at all.

    Not a refusal — nobody holds the vault; the lock itself could not be operated. A
    vault root that is not there, a lock directory that cannot be created or used, or a
    filesystem that will not take an `flock`.
    There is no holder to name, so there is no `locked` record either: a caller that
    branched on one would read this as a run it may retry, when what it needs is a
    human. Stdout stays empty rather than carrying a report the run never produced.

    For the CLIs whose output *is* a report — `kboat-lifecycle`, `kboat-pick set`,
    `kboat-repos refresh`, `kboat-repos backfill --apply`,
    `kboat-note migrate-slugs --apply`. Their contract is JSON on stdout and a
    diagnostic on stderr, and an uncaught `OSError` from acquisition would break
    it with a traceback and no output at all. `kboat-note`
    is on both sides of this: `migrate-slugs` reports on a whole vault and comes
    here, while `write` is a note writer and does not. For the two note writers,
    `run_write` already folds an unusable lock into its `write failed: …`, which is
    the right shape for a CLI whose whole output is the fate of one write.
    """
    sys.stderr.write(f"vault lock unavailable: {exc}\n")
    return 1


def _read_json_record() -> dict:
    # Decoded strictly by hand, since `sys.stdin`'s error handler follows the locale
    # (`surrogateescape` under `C`/`POSIX`, PEP 540). An `OSError` is left to `run_write`'s
    # exit 1, since it is not a record to fix.
    try:
        text = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as e:
        raise BadInputError(f"stdin is not valid UTF-8: {e}") from e
    try:
        record = json.loads(text)
    except json.JSONDecodeError as e:
        raise BadInputError(f"stdin is not valid JSON: {e}") from e
    if not isinstance(record, dict):
        raise BadInputError("record must be a JSON object")
    # JSON's `\u` escape admits a lone surrogate, which a UTF-8 note cannot hold, so the whole
    # record is checked here rather than wherever one field happens to fail.
    try:
        json.dumps(record, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as e:
        raise BadInputError(f"record holds a lone surrogate: {e}") from e
    return record


def run_write(write: Callable[[dict], dict[str, object]]) -> int:
    """Hand the JSON record on stdin to `write`, print its result, and return the exit code.

    Reading stdin here rather than in the caller is what makes exit 2 reachable:
    a `BadInputError` raised while decoding the record has to be caught by the
    same frame that maps it. `write` returns an `upsert` result — it is read for
    `status`, a key an unwritten note still carries.

    Exit 2 is a record to fix, 1 a write that did not happen (a failure, a
    refusal, or a vault another run holds), 0 a note on disk. Success is named
    rather than the refusals: a status this mapping has not heard of is one it
    cannot claim wrote a note.

    Stdin that is not UTF-8, and a record holding a lone surrogate in any key or value, exit
    2; an `OSError` reading stdin exits 1.
    """
    try:
        result = write(_read_json_record())
    except BadInputError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    except VaultLockedError as e:
        return emit_locked(e)
    except NOTE_READ_ERRORS as e:
        sys.stderr.write(f"write failed: {e}\n")
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result["status"] in WROTE_A_NOTE else 1
