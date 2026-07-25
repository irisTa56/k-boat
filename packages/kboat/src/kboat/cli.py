"""Shared plumbing for the note-writing entry points.

`kboat-note write` and `kboat-repos write` are two contracts over one writer:
they differ in the record shape they accept and must otherwise behave
identically — same flags, same exit codes, same diagnostics, same JSON on
stdout. Holding that here is what makes "identically" a fact rather than a
convention two files happen to share.
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


def add_write_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--vault",
        default=os.environ.get("OBSIDIAN_VAULT_PATH"),
        help="Obsidian vault root (defaults to $OBSIDIAN_VAULT_PATH).",
    )
    parser.add_argument(
        "--today",
        default=date.today().isoformat(),
        help="Override today's date (YYYY-MM-DD); for testing and reproducibility.",
    )


def vault_path(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Path:
    """The validated vault root; a usage error (exit 2) if the arguments are unusable."""
    if not args.vault:
        parser.error("no vault: pass --vault or set OBSIDIAN_VAULT_PATH")
    try:
        date.fromisoformat(args.today)
    except ValueError:
        parser.error(f"--today must be YYYY-MM-DD, got {args.today!r}")
    return Path(args.vault).expanduser()


def read_json_record() -> dict:
    """The JSON object on stdin. Raises `BadInputError` if it is not one."""
    try:
        record = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        raise BadInputError(f"stdin is not valid JSON: {e}") from e
    if not isinstance(record, dict):
        raise BadInputError("record must be a JSON object")
    return record


def run_write(write: Callable[[], Mapping[str, object]]) -> int:
    """Run one write, print its result as JSON, and return the process exit code.

    Exit 2 is a record to fix, 1 a write that did not happen (a failure, or a
    refused collision), 0 a note on disk.
    """
    try:
        result = write()
    except BadInputError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    except (FrontmatterError, OSError) as e:
        sys.stderr.write(f"write failed: {e}\n")
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if result["status"] == "collision" else 0
