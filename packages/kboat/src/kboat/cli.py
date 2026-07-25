"""Shared plumbing for the note-writing entry points.

`kboat-note write` and `kboat-repos write` are two contracts over one writer.
What they share is the shape of the transaction — the same flags, one JSON
record on stdin, results on stdout and diagnostics on stderr, and one mapping
from outcome to exit code — and holding it here is what keeps that from
drifting apart. What each keeps is its own: the record shape it accepts, the
diagnostics for a record it refuses, and any key it adds to the result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path

from kboat.frontmatter import FrontmatterError


class BadInputError(Exception):
    """A record the CLI refuses to act on: reported on stderr, exit 2.

    Separate from a write failure (exit 1) because the caller's response
    differs: a malformed record is the agent's to fix and re-send, where a
    failed write is the vault's to repair.
    """


def _iso_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"must be YYYY-MM-DD, got {value!r}") from e
    return value


def add_write_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--vault",
        default=os.environ.get("OBSIDIAN_VAULT_PATH"),
        help="Obsidian vault root (defaults to $OBSIDIAN_VAULT_PATH).",
    )
    parser.add_argument(
        "--today",
        type=_iso_date,
        default=date.today().isoformat(),
        help="Override today's date (YYYY-MM-DD); for testing and reproducibility.",
    )


def vault_path(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Path:
    """The vault root, or a usage error (exit 2) when neither flag nor env gives one."""
    if not args.vault:
        parser.error("no vault: pass --vault or set OBSIDIAN_VAULT_PATH")
    return Path(args.vault).expanduser()


def _read_json_record() -> dict:
    try:
        record = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        raise BadInputError(f"stdin is not valid JSON: {e}") from e
    if not isinstance(record, dict):
        raise BadInputError("record must be a JSON object")
    return record


def run_write(write: Callable[[dict], Mapping[str, object]]) -> int:
    """Hand the JSON record on stdin to `write`, print its result, and return the exit code.

    Reading stdin here rather than in the caller is what makes exit 2 reachable:
    a `BadInputError` raised while decoding the record has to be caught by the
    same frame that maps it. `write` returns an `upsert` result — it is read for
    `status`, a key an unwritten note still carries.

    Exit 2 is a record to fix, 1 a write that did not happen (a failure, or a
    refused collision), 0 a note on disk.
    """
    try:
        result = write(_read_json_record())
    except BadInputError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    except (FrontmatterError, OSError) as e:
        sys.stderr.write(f"write failed: {e}\n")
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if result["status"] == "collision" else 0
