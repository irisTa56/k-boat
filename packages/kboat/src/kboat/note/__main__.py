"""CLI entry point: `kboat-note <subcommand>`.

- `write --type {source,kindle,repo,feed}` — create or update a vault note from a
  `{slug, fields, body?}` JSON record on stdin, schema-driven via `kboat.write`
  (`upsert`): frontmatter order, YAML quoting, the always-present fields, body
  preservation, slug de-dup, and the `added_date`/`refreshed_date` stamps are all
  guaranteed by the package, so the agent never hand-writes frontmatter. Prints
  the result (`{status, slug, path}`, or a `collision`) as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from kboat.frontmatter import FrontmatterError
from kboat.schema import BY_TYPE
from kboat.write import upsert


def _write(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-note write",
        description="Create or update a vault note from a JSON record on stdin.",
    )
    parser.add_argument("--type", required=True, choices=sorted(BY_TYPE))
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
    args = parser.parse_args(argv)

    if not args.vault:
        parser.error("no vault: pass --vault or set OBSIDIAN_VAULT_PATH")
    try:
        date.fromisoformat(args.today)
    except ValueError:
        parser.error(f"--today must be YYYY-MM-DD, got {args.today!r}")

    try:
        record = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"stdin is not valid JSON: {e}\n")
        return 2
    if not isinstance(record, dict) or "slug" not in record:
        sys.stderr.write("record must be a JSON object with a 'slug' key\n")
        return 2

    try:
        result = upsert(BY_TYPE[args.type], Path(args.vault).expanduser(), record, today=args.today)
    except (FrontmatterError, OSError) as e:
        sys.stderr.write(f"write failed: {e}\n")
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if result["status"] == "collision" else 0


_COMMANDS = {"write": _write}


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
