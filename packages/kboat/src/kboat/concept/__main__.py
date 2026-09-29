"""CLI entry point: `kboat-concept`.

`kboat-concept shape <file_path>` reads the concept note at `<file_path>` under the
knowledge root and prints the `{shape}` record kboat-distill branches on before it
adds a reading group. The root defaults to `$KBOAT_KNOWLEDGE_PATH`.

Text carrying no `## Observations` heading is refused rather than answered, because
it is not a concept note and the shapes are answers about one. The refusal exits
**2**, the code `kboat.cli` reserves for a record the caller has to fix and the one
argparse already uses for a usage error: what was handed in is the caller's to
correct. A path with no file behind it exits 2 for the same reason, being either a
path Basic Memory did not return or a root set wrong. A read the OS refuses and a
file that is not UTF-8 exit **1**, this package's shape for an operation that did
not happen for a reason outside the caller, which a run reading it would treat very
differently.

A path rather than the text on stdin: a call carrying the note in a here-doc grows
with the note, and Claude Code prompts for any command over 10,000 characters however
its allow rules read, which concept notes already reach. A path keeps the call short
and its root out of it, so the one rule `kboat-concept shape:*` approves it. The path
is the project-relative `file_path` Basic Memory's `read_note` returns, so there is
no filename transform and no title resolution; the caller single-quotes it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from kboat.concept import classify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-concept",
        description="Answer about one concept note's structure.",
    )
    parser.add_argument(
        "--knowledge",
        default=os.environ.get("KBOAT_KNOWLEDGE_PATH"),
        help="Knowledge base root (defaults to $KBOAT_KNOWLEDGE_PATH).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    shape_parser = subparsers.add_parser(
        "shape",
        help="Classify the Observations section of one concept note.",
        description=(
            "Read the concept note at FILE_PATH under the knowledge root and print "
            '{"shape": "flat"|"grouped"} as JSON.'
        ),
    )
    shape_parser.add_argument(
        "file_path",
        help="The note's path relative to the knowledge root, as read_note returns it.",
    )
    args = parser.parse_args(argv)

    if not args.knowledge:
        parser.error("no knowledge base: pass --knowledge or set KBOAT_KNOWLEDGE_PATH")
    path = Path(args.knowledge).expanduser() / args.file_path

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        sys.stderr.write(f"no such note: {path}\n")
        return 2
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"could not read {path}: {exc}\n")
        return 1

    shape = classify(text)
    if shape is None:
        sys.stderr.write(
            f"no concept note at {path}: the text carries no '## Observations' heading\n"
        )
        return 2
    json.dump({"shape": shape}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
