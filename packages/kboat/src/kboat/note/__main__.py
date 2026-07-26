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
import sys

from kboat.cli import (
    BadInputError,
    add_today_argument,
    add_vault_argument,
    require_readable_payload,
    run_write,
    vault_path,
)
from kboat.schema import BY_TYPE
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
        if "slug" not in record:
            raise BadInputError("record must carry a 'slug' key")
        require_readable_payload(record)
        return upsert(BY_TYPE[args.type], vault, record, today=args.today)

    return run_write(write)


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
