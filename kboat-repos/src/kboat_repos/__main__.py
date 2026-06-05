"""CLI entry point: `kboat-repos <subcommand>`.

Three subcommands, all printing JSON on stdout:

- `gather <url>` — one repo's canonical identity, ready-to-write `fields`, and
  README excerpt (for ingest-time classification by the `kboat-repos` skill).
- `write` — assemble and write a `Repos/<slug>.md` note from a gather record +
  classification (JSON on stdin), so the agent never hand-writes frontmatter.
- `refresh` — re-fetch every `Repos/*.md` note's GitHub-derived frontmatter and
  recompute `status`, adopting renames, preserving the judgement layer and body.

The one-time migration of the legacy catalogue is deliberately NOT a subcommand
— it was a throwaway script that imports this package's helpers, run once and
deleted, so no single-use code lives here.
"""

from __future__ import annotations

import sys

from . import gather, refresh, write

_COMMANDS = {"gather": gather.main, "write": write.main, "refresh": refresh.main}


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
