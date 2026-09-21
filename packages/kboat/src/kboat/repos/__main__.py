"""CLI entry point: `kboat-repos <subcommand>`.

Four subcommands, all printing JSON on stdout:

- `gather <url>` — one repo's canonical identity, ready-to-write `fields`, and
  README excerpt (for ingest-time classification by the `kboat-repos` skill).
- `write` — assemble and write a `Repos/<slug>.md` note from a gather record +
  classification (JSON on stdin), so the agent never hand-writes frontmatter.
- `refresh` — re-fetch every `Repos/*.md` note's GitHub-derived frontmatter and
  recompute `status`, adopting renames, preserving the judgement layer and body.
- `backfill-readme --dry-run|--apply` — write `readme: unknown` on every repo
  note that has no `readme` line.

The one-time migration of the legacy catalogue is deliberately NOT a subcommand
— it was a throwaway script that imports this package's helpers, run once and
deleted. `backfill-readme` is the exception: it rewrites every note in the live
catalogue, so it is tested like the rest rather than run as an untested script,
and it is run again for any note that was evicted the first time.
"""

from __future__ import annotations

import sys

from . import backfill, gather, refresh, write

_COMMANDS = {
    "gather": gather.main,
    "write": write.main,
    "refresh": refresh.main,
    "backfill-readme": backfill.main,
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        sys.stderr.write(f"usage: kboat-repos {{{','.join(_COMMANDS)}}} ...\n")
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
