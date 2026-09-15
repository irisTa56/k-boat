"""CLI entry point: `kboat-knowledge`.

- `titles` -- print `{"flagged": [{"file", "title"}]}`: every concept note whose title
  a filename-resolved wikilink cannot reach.
- `tags` -- print `{"counts": {tag: n}, "untagged": [file]}`: the facet-tag census,
  most used first, and the concept notes that carry no tag.

Both read `<knowledge root>/concepts/*.md`, the root defaulting to
`$KBOAT_KNOWLEDGE_PATH`, and neither writes.

A root with no `concepts/` directory is a usage error (exit 2) rather than an empty
answer: the likeliest cause is a mis-set root, and an empty census would read as a
base with nothing in it. A listing or a read the OS refuses, and a concept note that
is an iCloud placeholder, exit 1 with an empty stdout and the cause on stderr, since
the audit did not happen for a reason outside the caller.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from kboat.knowledge import (
    CONCEPTS_DIR,
    EvictedNotesError,
    flagged_titles,
    read_concepts,
    tag_census,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-knowledge",
        description="Audit the knowledge base's concept notes (read-only).",
    )
    parser.add_argument(
        "--knowledge",
        default=os.environ.get("KBOAT_KNOWLEDGE_PATH"),
        help="Knowledge base root (defaults to $KBOAT_KNOWLEDGE_PATH).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "titles",
        help="List concept notes whose title a filename-resolved wikilink cannot reach.",
    )
    sub.add_parser("tags", help="Count concept-note facet tags and list the untagged notes.")
    args = parser.parse_args(argv)

    if not args.knowledge:
        parser.error("no knowledge base: pass --knowledge or set KBOAT_KNOWLEDGE_PATH")
    root = Path(args.knowledge).expanduser()
    if not (root / CONCEPTS_DIR).is_dir():
        parser.error(f"no {CONCEPTS_DIR}/ directory under {root}")

    try:
        notes = read_concepts(root)
    except EvictedNotesError as exc:
        sys.stderr.write(f"{exc}; download them and re-run:\n")
        for path in exc.placeholders:
            sys.stderr.write(f"  {path}\n")
        return 1
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"could not read the knowledge base: {exc}\n")
        return 1

    output = {"flagged": flagged_titles(notes)} if args.command == "titles" else tag_census(notes)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
