"""CLI entry point: `kboat-concept`.

`kboat-concept shape` reads a concept note's markdown on stdin and prints the
`{shape}` record kboat-distill branches on before it adds a reading group. Text
carrying no `## Observations` heading is refused rather than answered, because it is
not a concept note and the shapes are answers about one. The refusal exits **2**, the
code `kboat.cli` reserves for a record the caller has to fix and the one argparse
already uses for a usage error: what was handed in is the caller's to correct. Exit 1
is this package's shape for an operation that did not happen for a reason outside the
caller, which a run reading it would treat very differently.

Stdin rather than a title or a path: the writer has just read the note through
Basic Memory and already holds the text, so taking it directly keeps the tool off
the knowledge root entirely -- no filename transform, no title resolution, no
iCloud probe, and no shell quoting of a title that may hold anything.
"""

from __future__ import annotations

import argparse
import json
import sys

from kboat.concept import classify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-concept",
        description="Answer about one concept note's structure, read from stdin.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "shape",
        help="Classify the Observations section of the concept note read from stdin.",
        description=(
            "Read a concept note's markdown on stdin and print "
            '{"shape": "flat"|"grouped"} as JSON.'
        ),
    )
    parser.parse_args(argv)

    shape = classify(sys.stdin.read())
    if shape is None:
        sys.stderr.write(
            "no concept note on stdin: the text carries no '## Observations' heading\n"
        )
        return 2
    json.dump({"shape": shape}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
